## Self-organizing map base code including training and fitting data, along with limited plotting capabilities
## ported from the POPSOM library by Li Yuan (2018)
## https://github.com/njali2001/popsom.git 
## with modifications by Trung Ha (2024) for aweSOM


import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
# plt.rcParams.update({'font.size': 8})
import seaborn as sns					
from random import randint
from sklearn.metrics.pairwise import euclidean_distances
import statsmodels.stats.api as sms     # t-test
import statistics as stat               # F-test
from scipy import stats                 # KS Test
from scipy.stats import f               # F-test
from itertools import combinations

from numba import njit, prange
from scipy.ndimage import map_coordinates

seed = 42
np.random.seed(seed)

class Lattice:
	def __init__(self, xdim : int = 10, ydim : int = 10, alpha_0 : float = 0.3, train : int = 1000, alpha_type : str = "decay", sampling_type : str = "sampling"):
		""" Initialize the SOM lattice. - CHECK GOOD
			
		Args:
			xdim (int): The x dimension of the map. Default is 10.
			ydim (int): The y dimension of the map. Default is 10.
			alpha_0 (float): The initial learning rate, should be a positive non-zero real number. Default is 0.3.
			train (int): Number of total training iterations; include for all batches. Default is 1000.
			alpha_type (str): A string that determines whether the learning rate is static or decaying. Default is "decay".
			sampling_type (str): A string that determines whether the initial lattice is uniform or randomly sampled from the data. Default is "sampling".

    	"""
		self.xdim = xdim
		self.ydim = ydim
		self.alpha = alpha_0
		self.train = train
		self.init = sampling_type # uniform or random sampling initial lattice
		self.seed = seed
		self.epoch = 0

		if alpha_type == "static":
			self.alpha_type = 0
			self.alpha_0 = alpha_0
		elif alpha_type == "decay":
			self.alpha_type = 1
			self.alpha_0 = alpha_0
		else:
			sys.exit("alpha_type must be either 'static' or 'decay'")
		
		self.save_frequency = self.train // 200 # how often to save the node weights
		self.lattice_history = []
		self.umat_history = []

	def train_lattice(self, data : np.ndarray, features_names : list[str], labels : np.ndarray = None, number_of_steps : int = -1, save_lattice : bool = False, restart_lattice : np.ndarray = None):
		""" Train the Model with numba JIT acceleration.

		Args:
			data (np.ndarray): A numpy 2D array where each row contains an unlabeled training instance.
			features_names (list[str]): A list of feature names.
			labels (np.ndarray, optional): A vector or dataframe with one label for each observation in data. Defaults to None.
			number_of_steps (int): Number of steps taken this batch, used for keeping track of training restarts. Default is self.train.
			save_lattice (bool, optional): A flag that determines whether the node weights are saved to a file at the end of training. Defaults to False.
			restart_lattice (np.ndarray, optional): Vectors for the weights of the nodes from past realizations. Defaults to None.

    	"""
		
		self.restart_lattice = restart_lattice
		self.save_lattice = save_lattice

		self.data_array = data
		self.features_names = features_names
		self.labels = labels
		if number_of_steps == -1:
			self.this_batch_train = self.train
		else:
			self.this_batch_train = number_of_steps
		# self.momentum_decay_rate = momentum_decay_rate

		# check if the dims are reasonable
		if (self.xdim < 4 or self.ydim < 4):
			sys.exit("build: map is too small.")

		# train SOM
		self.fast_som()
	

	def fast_som(self):
		""" Performs the self-organizing map (SOM) training. - CHECK GOOD

		This method initializes the SOM with random values or a subset of the data, and then trains the SOM by updating the
		node weights based on the input vectors. The training process includes adjusting the learning rate, shrinking the
		neighborhood size, and saving the node weights and U-matrix periodically.

		"""
    	# some constants
		number_input_vectors = self.data_array.shape[0]
		number_features = self.data_array.shape[1]
		number_nodes = self.xdim * self.ydim
		this_batch_train = self.this_batch_train # only train for this number of steps; useful for restarting training

		if self.restart_lattice is not None:
			lattice = self.restart_lattice
		else:
			if self.init == "uniform":
				# vector with small init values for all nodes
				# NOTE: each row represents a node, each column represents a feature.
				lattice = np.random.uniform(0., 1., (number_nodes,number_features))
			else:
				# sample a random subset of the data to initialize the lattice
				ix = np.random.randint(0, number_input_vectors-1, number_nodes)
				lattice = self.data_array[ix,:]
			
			self.lattice = lattice.copy()
			self.lattice_history.append(lattice) # save the initial lattice
			self.umat_history.append(self.compute_umat()) # save the initial U-matrix

		alpha = self.alpha # starting learning rate
		if self.alpha_type == 1:
			alpha_freq = self.train // 25 # how often to decay the learning rate; at 24 steps, alpha_f ~ 1e-3 alpha_0
		else:
			alpha_freq = 1

	    # compute the initial neighborhood size and step
		nsize_max = max(self.xdim, self.ydim) + 1
		nsize_min = 8
		nsize_step = this_batch_train // 4 # for the third quarter of the training steps, shrink the neighborhood
		nsize_freq = nsize_step // (nsize_max - nsize_min) # how often to shrink the neighborhood

		# if self.epoch < 2 * nsize_step:
		# 	nsize = nsize_max
		# elif self.epoch >= 3 * nsize_step:
		# 	nsize = nsize_min
		# else:
		# 	nsize = nsize_max - self.epoch // nsize_freq
		nsize = nsize_max # start with the largest neighborhood size at each training batch
   
		epoch = self.epoch  # counts the number of epochs per nsize_freq
		stop_epoch = epoch + this_batch_train
		print("starting epoch is: ", epoch, flush=True)
		print("stopping epoch is: ", stop_epoch, flush=True)

		print("Saving lattice every ", self.save_frequency, " epochs", flush=True)

	    # constants for the Gamma function
		m = np.reshape(list(range(number_nodes)), (number_nodes,1))  # a vector with all node 1D addresses

	    # x-y coordinate of ith node: m2Ds[i,] = c(xi, yi)
		m2Ds = self.coordinate(m, self.xdim)

		# this ensures that the same random order is not repeated if the training is restarted
		if self.epoch > 1:
			self.seed += 1
			np.random.seed(self.seed)

		# Added 06/17/2024: use random number generator, shuffle the data and take the first train samples
		rng = np.random.default_rng()
		indices = np.arange(number_input_vectors)
		rng.shuffle(indices)

		# Added 06/25/2024: if the number of training steps is larger than the number of data points, repeat the shuffled indices
		if this_batch_train > number_input_vectors:
			indices = np.tile(indices, this_batch_train // number_input_vectors + 1)
		xk = self.data_array[indices[:this_batch_train],:]

		# implement momentum-based gradient descent
		# momentum_decay_rate = self.momentum_decay_rate

		# history of the loss function
		# loss_freq = 1000
		# self.loss_history = np.zeros((self.train, number_features))
		# self.average_loss = np.zeros((self.train//loss_freq, number_features))

		print("Begin training", flush=True)
		while True:
			if epoch % int(self.train//10) == 0:
				print("Evaluating epoch = ", epoch, flush=True)

			# if (epoch % loss_freq == 0) & (epoch != 0):
			# 	this_average_loss = np.mean(self.loss_history[epoch-loss_freq:epoch], axis=0) # average loss over the last [loss_freq] epochs
			# 	self.average_loss[epoch//loss_freq-1,:] = this_average_loss

			# if training step has gone over the step limit, terminate
			if epoch >= stop_epoch:
				print("Terminating from step limit reached at epoch ", epoch, flush=True)
				self.epoch = epoch
				if self.save_lattice:
					print("Saving final lattice", epoch, flush=True)
					np.save(f"lattice_{epoch}_{self.xdim}{self.ydim}_{self.alpha}_{self.train}.npy", lattice)
				break

	        # get one random input vector
			xk_m = xk[epoch-self.epoch,:]
			
			# calculate the relative distance between input vector and nodes, take the closest node as the BMU
			# momentum = diff * momentum_decay_rate # momentum-based gradient descent
			diff = lattice - xk_m
			squ = diff**2
			s = np.sum(squ, axis=1)
			c = np.argmin(s)

			# self.loss_history[epoch,:] = np.sqrt(s[c])

	        # update step
			gamma_m = np.outer(self.Gamma(c, m2Ds, alpha, nsize), np.ones(number_features))
			# lattice -= (diff + momentum) * gamma_m
			lattice -= diff * gamma_m

			# shrink the neighborhood size every [nsize_freq] epochs
			if (epoch-self.epoch) > 2*nsize_step and (epoch-self.epoch) % nsize_freq == 0 and nsize > nsize_min:
				nsize = nsize_max - ((epoch-self.epoch) - 2*nsize_step) // nsize_freq
				print(f"Shrinking neighborhood size to {nsize} at epoch {epoch}", flush=True)

			# decay the learning rate every [alpha_freq] epochs
			if epoch % alpha_freq == 0 and self.alpha_type == 1 and epoch != 0:
				alpha *= 0.75
				print(f"Decaying learning rate to {alpha} at epoch {epoch}", flush=True)
    
			# save lattice sparingly
			if epoch % self.save_frequency == 0:
				self.lattice = lattice.copy()
				# print("Saving lattice at epoch ", epoch, flush=True)
				self.lattice_history.append(self.lattice)

				# compute the umatrix and save it
				umat = self.compute_umat()
				# np.save(f"umat_{epoch}_{self.xdim}{self.ydim}_{self.alpha}_{self.train}.npy", umat)
				self.umat_history.append(umat)
    
			epoch += 1
		print("Training complete", flush=True)

		# update the learning rate, lattice, and Umatrix after training
		self.alpha = alpha
		self.umat = self.compute_umat()
		self.lattice = lattice # lattice takes the shape of [X*Y, F]

	@staticmethod
	@njit()
	def Gamma(index_bmu : int, m2Ds : np.ndarray, alpha : float, nsize : int, gaussian : bool = True):
		""" Calculate the neighborhood function for a given BMU on a lattice. - CHECK GOOD

		Args:
			index_bmu (int): The index of the BMU node on the lattice.
			m2Ds (np.ndarray): Lattice coordinate of each node.
			alpha (float): The amplitude parameter for the Gaussian function, AKA the learning rate.
			nsize (int): The size of the neighborhood.
			gaussian (bool, optional): Whether to use Gaussian function or not. Defaults to True.

		Returns:
			np.ndarray: The neighborhood function values for each node on the grid.
		"""

		
		dist_2d = np.abs(m2Ds[index_bmu,:] - m2Ds) # 2d distance between the BMU and the rest of the lattice
		chebyshev_dist = np.zeros(dist_2d.shape[0]) # initialize the Chebyshev distance array
		for i in prange(dist_2d.shape[0]): # numba max does not have axis argument, otherwise this would be chebyshev_dist = np.max(dist_2d, axis=1)
			chebyshev_dist[i] = np.max(dist_2d[i,:])
  
		# Define the Gaussian function to calculate the neighborhood function
		def gauss(dist, A, sigma):
			""" gauss -- Gaussian function"""
			return A * np.exp(-(dist) ** 2 / (2 * sigma ** 2))
		
		# if a node on the lattice is in within nsize neighborhood, then h = Gaussian(alpha), else h = 0.0
		if gaussian: # use Gaussian function
			h = gauss(chebyshev_dist, alpha, nsize/3)
		else: # otherwise everything within nsize is multiplied by alpha, and everything outside is unchanged
			h = np.where(chebyshev_dist <= nsize, alpha, 0.)
		
		h[chebyshev_dist > nsize] = 0. # manually set the values outside the neighborhood to 0

		return h
	
	def map_data_to_lattice(self):
		"""
		After training, map each data point to the nearest node in the lattice. - CHECK GOOD

		Returns:
			np.ndarray[int]: A 2D array with the x and y coordinates of the best matching nodes for each data point.
		"""

		print("Begin matching points with nodes", flush=True)
		data_to_lattice_1d = self.best_match(self.lattice, self.data_array)

		self.projection_1d = data_to_lattice_1d

		projection_1d_to_2d = np.reshape(self.projection_1d,(len(self.projection_1d),1)) # make it a 2D array

		projection_2d = self.coordinate(projection_1d_to_2d, self.xdim)

		return projection_2d
	
	def assign_cluster_to_lattice(self, smoothing=None, merge_cost=0.005):
		"""
		Assigns clusters to the lattice based on the computed centroids. - CHECK GOOD

		Args:
			smoothing (float, optional): Smoothing parameter for computing Umatrix. Defaults to None.
			merge_cost (float, optional): Cost threshold for merging similar centroids. Defaults to 0.005.

		Returns:
			numpy.ndarray: Array representing the assigned clusters for each lattice point.
		"""
		
		umat = self.umat 
		naive_centroids = self.compute_centroids(False) # all local minima are centroids
		centroids = self.merge_similar_centroids(naive_centroids, merge_cost) # merge similar centroids
		
		x = self.xdim
		y = self.ydim
		centr_locs = []

		#create list of centroid locations
		for ix in range(x):
			for iy in range(y):
				cx = centroids['centroid_x'][ix, iy]
				cy = centroids['centroid_y'][ix, iy]
					
				centr_locs.append((cx,cy))


		unique_ids = list(set(centr_locs))
		n_clusters = len(unique_ids)
		print(f"Number of clusters : {n_clusters}", flush=True)
		print("Centroids: ", unique_ids, flush=True)

		# mapping = {}
		clusters = 1000 * np.ones((x,y), dtype=np.int32)
		for i in range(n_clusters):
			# mapping[i] = unique_ids[i]
			for ix in range(x):
				for iy in range(y):
					if (centroids['centroid_x'][ix, iy], centroids['centroid_y'][ix, iy]) == unique_ids[i]:
						clusters[ix,iy] = i

		self.lattice_assigned_clusters = clusters
		return clusters
	
	@staticmethod
	@njit(parallel=True)
	def assign_cluster_to_data(projection_2d : np.ndarray, clusters_on_lattice : np.ndarray) -> np.ndarray:
		"""
		Given a lattice and cluster assignments on that lattice, return the cluster ids of the data (in a 1d array)

		Args:
			projection_2d (np.ndarray): 2d array with x-y coordinates of the node associated with each data point
			clusters_on_lattice (np.ndarray): X x Y matrix of cluster labels on lattice

		Returns:
			np.ndarray: cluster_id of each data point
		"""
		cluster_id = np.zeros(projection_2d.shape[0], dtype=np.int32)
		for i in prange(projection_2d.shape[0]):
			cluster_id[i] = clusters_on_lattice[int(projection_2d[i,0]), int(projection_2d[i,1])]
		return cluster_id

	@staticmethod
	@njit(parallel=True)
	def best_match(lattice : np.ndarray, obs : np.ndarray, full=False) -> np.ndarray:
		"""
		Given input vector inp[n,f] (where n is number of different observations, f is number of features per observation), return the best matching node. - CHECK GOOD

		Args:
			lattice (np.ndarray): weight values of the lattice
			obs (np.ndarray): observations (input vectors)
			full (bool, optional): indicate whether to return first and second best match. Defaults to False.

		Returns:
			np.ndarray: return the 1d index of the best-matched node (within the lattice) for each observation
		"""

		if full:
			best_match_node = np.zeros((obs.shape[0], 2))
		else:
			best_match_node = np.zeros((obs.shape[0],1))

		for i in prange(obs.shape[0]):
			diff = lattice - obs[i,:]
			squ = diff ** 2
			s = np.sum(squ, axis=1)

			if full:
				# numba does not support argsort, so we record the top two best matches this way
				o = np.argmin(s)
				best_match_node[i,0] = o
				s[o] = np.max(s)
				o = np.argmin(s)
				best_match_node[i,1] = o
			else:
				best_match_node[i] = np.argmin(s)

			if i % int(obs.shape[0]//10) == 0:
				print("i = ", i)

		return best_match_node

	@staticmethod
	@njit(parallel=True)
	def coordinate(rowix : np.ndarray, xdim : int) -> np.ndarray:
		"""
		Convert from a list of row index to an array of xy-coordinates. - CHECK GOOD

		Args:
			rowix (np.ndarray): 1d array with the 1d indices of the points of interest (n x 1 matrix)
			xdim (int): x dimension of the lattice

		Returns:
			np.ndarray: array with x and y coordinates of each point in rowix
		"""

		len_rowix = len(rowix)
		coords = np.zeros((len_rowix, 2), dtype=np.int32)

		for k in prange(len_rowix):
			coords[k,:] = np.array([rowix[k,0] % xdim, rowix[k,0] // xdim])

		return coords

	def rowix(self, x, y):
		"""
		Convert from a xy-coordinate to a row index. - CHECK GOOD

		Args:
			x (int): The x-coordinate of the map.
			y (int): The y-coordinate of the map.

		Returns:
			int: The row index corresponding to the given xy-coordinate.

		"""
		rix = x + y*self.xdim
		return rix
	
	def node_weight(self, x, y):
		"""
		Returns the weight values of a node at (x,y) on the lattice. - CHECK GOOD

		Args:
			x (int): x-coordinate of the node.
			y (int): y-coordinate of the node.

		Returns:
			np.ndarray: 1d array of weight values of said node.
		"""

		ix = self.rowix(x, y)
		return self.lattice[ix]
				

	def compute_centroids(self, explicit=False):
		"""
		Compute the centroid for each node in the lattice given a precomputed Umatrix. - CHECK GOOD

		Args:
			explicit (bool): Controls the shape of the connected component.

		Returns:
			dict: A dictionary containing the matrices with the same x-y dimensions as the original map, 
			containing the centroid x-y coordinates.

		"""

		xdim = self.xdim
		ydim = self.ydim
		heat = self.umat
		centroid_x = np.array([[-1] * ydim for _ in range(xdim)])
		centroid_y = np.array([[-1] * ydim for _ in range(xdim)])

		def find_this_centroid(ix, iy):
			# recursive function to find the centroid of a point on the map

			if (centroid_x[ix, iy] > -1) and (centroid_y[ix, iy] > -1):
				return {"bestx": centroid_x[ix, iy], "besty": centroid_y[ix, iy]}

			min_val = heat[ix, iy]
			min_x = ix
			min_y = iy

			# (ix, iy) is an inner map element
			if ix > 0 and ix < xdim-1 and iy > 0 and iy < ydim-1:

				if heat[ix-1, iy-1] < min_val:
					min_val = heat[ix-1, iy-1]
					min_x = ix-1
					min_y = iy-1

				if heat[ix, iy-1] < min_val:
					min_val = heat[ix, iy-1]
					min_x = ix
					min_y = iy-1

				if heat[ix+1, iy-1] < min_val:
					min_val = heat[ix+1, iy-1]
					min_x = ix+1
					min_y = iy-1

				if heat[ix+1, iy] < min_val:
					min_val = heat[ix+1, iy]
					min_x = ix+1
					min_y = iy

				if heat[ix+1, iy+1] < min_val:
					min_val = heat[ix+1, iy+1]
					min_x = ix+1
					min_y = iy+1

				if heat[ix, iy+1] < min_val:
					min_val = heat[ix, iy+1]
					min_x = ix
					min_y = iy+1

				if heat[ix-1, iy+1] < min_val:
					min_val = heat[ix-1, iy+1]
					min_x = ix-1
					min_y = iy+1

				if heat[ix-1, iy] < min_val:
					min_val = heat[ix-1, iy]
					min_x = ix-1
					min_y = iy

			# (ix, iy) is bottom left corner
			elif ix == 0 and iy == 0:

				if heat[ix+1, iy] < min_val:
					min_val = heat[ix+1, iy]
					min_x = ix+1
					min_y = iy

				if heat[ix+1, iy+1] < min_val:
					min_val = heat[ix+1, iy+1]
					min_x = ix+1
					min_y = iy+1

				if heat[ix, iy+1] < min_val:
					min_val = heat[ix, iy+1]
					min_x = ix
					min_y = iy+1

			# (ix, iy) is bottom right corner
			elif ix == xdim-1 and iy == 0:

				if heat[ix, iy+1] < min_val:
					min_val = heat[ix, iy+1]
					min_x = ix
					min_y = iy+1

				if heat[ix-1, iy+1] < min_val:
					min_val = heat[ix-1, iy+1]
					min_x = ix-1
					min_y = iy+1

				if heat[ix-1, iy] < min_val:
					min_val = heat[ix-1, iy]
					min_x = ix-1
					min_y = iy

			# (ix, iy) is top right corner
			elif ix == xdim-1 and iy == ydim-1:

				if heat[ix-1, iy-1] < min_val:
					min_val = heat[ix-1, iy-1]
					min_x = ix-1
					min_y = iy-1

				if heat[ix, iy-1] < min_val:
					min_val = heat[ix, iy-1]
					min_x = ix
					min_y = iy-1

				if heat[ix-1, iy] < min_val:
					min_val = heat[ix-1, iy]
					min_x = ix-1
					min_y = iy

			# (ix, iy) is top left corner
			elif ix == 0 and iy == ydim-1:

				if heat[ix, iy-1] < min_val:
					min_val = heat[ix, iy-1]
					min_x = ix
					min_y = iy-1

				if heat[ix+1, iy-1] < min_val:
					min_val = heat[ix+1, iy-1]
					min_x = ix+1
					min_y = iy-1

				if heat[ix+1, iy] < min_val:
					min_val = heat[ix+1, iy]
					min_x = ix+1
					min_y = iy

			# (ix, iy) is a left side element
			elif ix == 0 and iy > 0 and iy < ydim-1:

				if heat[ix, iy-1] < min_val:
					min_val = heat[ix, iy-1]
					min_x = ix
					min_y = iy-1

				if heat[ix+1, iy-1] < min_val:
					min_val = heat[ix+1, iy-1]
					min_x = ix+1
					min_y = iy-1

				if heat[ix+1, iy] < min_val:
					min_val = heat[ix+1, iy]
					min_x = ix+1
					min_y = iy

				if heat[ix+1, iy+1] < min_val:
					min_val = heat[ix+1, iy+1]
					min_x = ix+1
					min_y = iy+1

				if heat[ix, iy+1] < min_val:
					min_val = heat[ix, iy+1]
					min_x = ix
					min_y = iy+1

			# (ix, iy) is a bottom side element
			elif ix > 0 and ix < xdim-1 and iy == 0:

				if heat[ix+1, iy] < min_val:
					min_val = heat[ix+1, iy]
					min_x = ix+1
					min_y = iy
	
				if heat[ix+1, iy+1] < min_val:
					min_val = heat[ix+1, iy+1]
					min_x = ix+1
					min_y = iy+1

				if heat[ix, iy+1] < min_val:
					min_val = heat[ix, iy+1]
					min_x = ix
					min_y = iy+1

				if heat[ix-1, iy+1] < min_val:
					min_val = heat[ix-1, iy+1]
					min_x = ix-1
					min_y = iy+1

				if heat[ix-1, iy] < min_val:
					min_val = heat[ix-1, iy]
					min_x = ix-1
					min_y = iy

			# (ix, iy) is a right side element
			elif ix == xdim-1 and iy > 0 and iy < ydim-1:

				if heat[ix-1, iy-1] < min_val:
					min_val = heat[ix-1, iy-1]
					min_x = ix-1
					min_y = iy-1

				if heat[ix, iy-1] < min_val:
					min_val = heat[ix, iy-1]
					min_x = ix
					min_y = iy-1

				if heat[ix, iy+1] < min_val:
					min_val = heat[ix, iy+1]
					min_x = ix
					min_y = iy+1

				if heat[ix-1, iy+1] < min_val:
					min_val = heat[ix-1, iy+1]
					min_x = ix-1
					min_y = iy+1

				if heat[ix-1, iy] < min_val:
					min_val = heat[ix-1, iy]
					min_x = ix-1
					min_y = iy

			# (ix, iy) is a top side element
			elif ix > 0 and ix < xdim-1 and iy == ydim-1:

				if heat[ix-1, iy-1] < min_val:
					min_val = heat[ix-1, iy-1]
					min_x = ix-1
					min_y = iy-1

				if heat[ix, iy-1] < min_val:
					min_val = heat[ix, iy-1]
					min_x = ix
					min_y = iy-1

				if heat[ix+1, iy-1] < min_val:
					min_val = heat[ix+1, iy-1]
					min_x = ix+1
					min_y = iy-1

				if heat[ix+1, iy] < min_val:
					min_val = heat[ix+1, iy]
					min_x = ix+1
					min_y = iy

				if heat[ix-1, iy] < min_val:
					min_val = heat[ix-1, iy]
					min_x = ix-1
					min_y = iy

	        # if successful
	        # move to the square with the smaller value, i_e_, call
	        # find_this_centroid on this new square
	        # note the RETURNED x-y coords in the centroid_x and
	        # centroid_y matrix at the current location
	        # return the RETURNED x-y coordinates

			if min_x != ix or min_y != iy:
				r_val = find_this_centroid(min_x, min_y)

	            # if explicit is set show the exact connected component
	            # otherwise construct a connected componenent where all
	            # nodes are connected to a centrol node
				if explicit:

					centroid_x[ix, iy] = min_x
					centroid_y[ix, iy] = min_y
					return {"bestx": min_x, "besty": min_y}

				else:
					centroid_x[ix, iy] = r_val['bestx']
					centroid_y[ix, iy] = r_val['besty']
					return r_val

			else:
				centroid_x[ix, iy] = ix
				centroid_y[ix, iy] = iy
				return {"bestx": ix, "besty": iy}

		for i in range(xdim):
			for j in range(ydim):
				find_this_centroid(i, j)

		return {"centroid_x": centroid_x, "centroid_y": centroid_y}

	@staticmethod
	# @njit(parallel=True) # numba does not support dictionary; so cannot parallelize this function
	def replace_value(centroids : dict[str, np.ndarray], centroid_a : tuple, centroid_b : tuple) -> dict[str, np.ndarray]:
		"""
		Replaces the values of centroid_a with the values of centroid_b in the given centroids dictionary. - CHECK GOOD

		Args:
			centroids (dict[str, np.ndarray]): A dictionary containing the centroids.
			centroid_a (tuple): The coordinates of the centroid to be replaced.
			centroid_b (tuple): The coordinates of the centroid to replace with.

		Returns:
			dict[str, np.ndarray]: The updated centroids dictionary.
		"""
		(xdim, ydim) = centroids['centroid_x'].shape
		for ix in range(xdim):
				for iy in range(ydim):
					if centroids['centroid_x'][ix, iy] == centroid_a[0] and centroids['centroid_y'][ix, iy] == centroid_a[1]:
						centroids['centroid_x'][ix, iy] = centroid_b[0]
						centroids['centroid_y'][ix, iy] = centroid_b[1]

		return centroids

	def merge_similar_centroids(self, naive_centroids : np.ndarray, threshold=0.3):
		""" 
		Merge centroids that are close enough together. - CHECK GOOD

		Args:
			naive_centroids (np.ndarray): original centroids before merging
			threshold (float, optional): Any centroids with pairwise cost less than this threshold is merged. Defaults to 0.3.

		Returns:
			np.ndarray: new node map with combined centroids
		"""

		heat = self.umat
		centroids = naive_centroids.copy()
		unique_centroids = self.get_unique_centroids(centroids) # the nodes_count dictionary is also created here, so don't remove this line

		# for each pair of centroids, compute the weighted distance between them via interpolating the umat
		# if the distance is less than the threshold, combine the centroids
  
		cost_between_centroids = []

		for i in range(len(unique_centroids['position_x'])-1):
			for j in range(i+1, len(unique_centroids['position_x'])):
				a = [unique_centroids['position_x'][i], unique_centroids['position_y'][i]]
				b = [unique_centroids['position_x'][j], unique_centroids['position_y'][j]]
				num_sample = 5*int(np.sqrt((b[0]-a[0])**2 + (b[1]-a[1])**2))
				x, y = np.linspace(a[0], b[0], num_sample), np.linspace(a[1], b[1], num_sample)
				umat_dist = map_coordinates(heat**2, np.vstack((x,y)))
				total_cost = np.sum(umat_dist)
				# total_cost /= num_sample
				cost_between_centroids.append([a, b, total_cost])
		
		# cost_between_centroids.sort(key=lambda x: x[2])  
		sorted_cost = sorted(cost_between_centroids, key=lambda x: x[2]) # this ranks all pairwise cost in ascending order
		# normalize the cost such that the largest cost at each step is always one
		sorted_cost = [[a, b, c/sorted_cost[-1][2]] for a, b, c in sorted_cost]

		# combine the centroids, going down the list until the threshold is reached
		if sorted_cost[0][2] < threshold:
			centroid_a = tuple(sorted_cost[0][0])
			centroid_b = tuple(sorted_cost[0][1])
			nodes_a = self.nodes_count[centroid_a]
			nodes_b = self.nodes_count[centroid_b]

			print("Centroid A: ", centroid_a, flush=True)
			print("Node A count: ", nodes_a, flush=True)
			print("Centroid B: ", centroid_b, flush=True)
			print("Node B count: ", nodes_b, flush=True)

			print("Merging...", flush=True)

			replace_a_with_b = False
			if nodes_a < nodes_b:
				replace_a_with_b = True

			if replace_a_with_b:
				centroids = self.replace_value(centroids, centroid_a, centroid_b)
			else:
				centroids = self.replace_value(centroids, centroid_b, centroid_a)

			# print("New centroids: \n", centroids, flush=True)
			centroids = self.merge_similar_centroids(centroids, threshold)
		else:
			unique_centroids = self.get_unique_centroids(centroids)
			print("Centroids: \n", unique_centroids, flush=True)
			print("Minimum cost between centroids: ", sorted_cost[0][2], flush=True)
			return centroids

		return centroids

	def get_unique_centroids(self, centroids):
		"""
		Print out a list of unique centroids given a matrix of centroid locations. - CHECK GOOD

		Args:
			centroids: A matrix of the centroid locations in the map.

		Returns:
			A dictionary containing the unique x and y positions of the centroids.
			The dictionary has the following keys:
				position_x: A list of unique x positions.
				position_y: A list of unique y positions.
		"""

		# get the dimensions of the map
		xdim = self.xdim
		ydim = self.ydim
		xlist = []
		ylist = []
		centr_locs = []

		# create a list of unique centroid positions
		for ix in range(xdim):
			for iy in range(ydim):
				cx = centroids['centroid_x'][ix, iy]
				cy = centroids['centroid_y'][ix, iy]
					
				centr_locs.append((cx,cy))

		self.nodes_count = {i:centr_locs.count(i) for i in centr_locs}

		unique_ids = list(set(centr_locs))
		xlist = [x for x, y in unique_ids]
		ylist = [y for x, y in unique_ids]

		return {"position_x": xlist, "position_y": ylist}
	

	def compute_umat(self, smoothing=None):
		"""
		Compute the unified distance matrix. - CHECK GOOD

		Args:
			smoothing (float, optional): A positive floating point value controlling the smoothing of the umat representation. Defaults to None.

		Returns:
			numpy.ndarray: A matrix with the same x-y dimensions as the original map containing the umat values.
		"""

		d = euclidean_distances(self.lattice, self.lattice) / (self.xdim*self.ydim)
		umat = self.compute_heat(d, smoothing)

		return umat

	def compute_heat(self, d, smoothing=None): # WORKS / CHECKED 04/17
		"""
		Compute a heat value map representation of the given distance matrix. - CHECK GOOD

		Args:
			d (numpy.ndarray): A distance matrix computed via the 'dist' function.
			smoothing (float, optional): A positive floating point value controlling the smoothing of the umat representation. Defaults to None.

		Returns:
			numpy.ndarray: A matrix with the same x-y dimensions as the original map containing the heat.
		"""

		x = self.xdim
		y = self.ydim
		heat = np.array([[0.0] * y for _ in range(x)])

		if x == 1 or y == 1:
			sys.exit("compute_heat: heat map can not be computed for a map \
	                 with a dimension of 1")

		# this function translates our 2-dim lattice coordinates
		# into the 1-dim coordinates of the lattice
		def xl(ix, iy):

			return ix + iy * x

		# check if the map is larger than 2 x 2 (otherwise it is only corners)
		if x > 2 and y > 2:
			# iterate over the inner nodes and compute their umat values
			for ix in range(1, x-1):
				for iy in range(1, y-1):
					sum = (d[xl(ix, iy), xl(ix-1, iy-1)] +
						   d[xl(ix, iy), xl(ix, iy-1)] +
	                       d[xl(ix, iy), xl(ix+1, iy-1)] +
	                       d[xl(ix, iy), xl(ix+1, iy)] +
	                       d[xl(ix, iy), xl(ix+1, iy+1)] +
	                       d[xl(ix, iy), xl(ix, iy+1)] +
	                       d[xl(ix, iy), xl(ix-1, iy+1)] +
	                       d[xl(ix, iy), xl(ix-1, iy)])

					heat[ix, iy] = sum/8

			# iterate over bottom x axis
			for ix in range(1, x-1):
				iy = 0
				sum = (d[xl(ix, iy), xl(ix+1, iy)] +
	                   d[xl(ix, iy), xl(ix+1, iy+1)] +
	                   d[xl(ix, iy), xl(ix, iy+1)] +
	                   d[xl(ix, iy), xl(ix-1, iy+1)] +
	                   d[xl(ix, iy), xl(ix-1, iy)])

				heat[ix, iy] = sum/5

			# iterate over top x axis
			for ix in range(1, x-1):
				iy = y-1
				sum = (d[xl(ix, iy), xl(ix-1, iy-1)] +
	                   d[xl(ix, iy), xl(ix, iy-1)] +
	                   d[xl(ix, iy), xl(ix+1, iy-1)] +
	                   d[xl(ix, iy), xl(ix+1, iy)] +
	                   d[xl(ix, iy), xl(ix-1, iy)])

				heat[ix, iy] = sum/5

			# iterate over the left y-axis
			for iy in range(1, y-1):
				ix = 0
				sum = (d[xl(ix, iy), xl(ix, iy-1)] +
	                   d[xl(ix, iy), xl(ix+1, iy-1)] +
	                   d[xl(ix, iy), xl(ix+1, iy)] +
	                   d[xl(ix, iy), xl(ix+1, iy+1)] +
	                   d[xl(ix, iy), xl(ix, iy+1)])

				heat[ix, iy] = sum/5

			# iterate over the right y-axis
			for iy in range(1, y-1):
				ix = x-1
				sum = (d[xl(ix, iy), xl(ix-1, iy-1)] +
	                   d[xl(ix, iy), xl(ix, iy-1)] +
	                   d[xl(ix, iy), xl(ix, iy+1)] +
	                   d[xl(ix, iy), xl(ix-1, iy+1)] +
	                   d[xl(ix, iy), xl(ix-1, iy)])

				heat[ix, iy] = sum/5

		# compute umat values for corners
		if x >= 2 and y >= 2:
			# bottom left corner
			ix = 0
			iy = 0
			sum = (d[xl(ix, iy), xl(ix+1, iy)] +
	               d[xl(ix, iy), xl(ix+1, iy+1)] +
	               d[xl(ix, iy), xl(ix, iy+1)])

			heat[ix, iy] = sum/3

			# bottom right corner
			ix = x-1
			iy = 0
			sum = (d[xl(ix, iy), xl(ix, iy+1)] +
	               d[xl(ix, iy), xl(ix-1, iy+1)] +
	               d[xl(ix, iy), xl(ix-1, iy)])
			heat[ix, iy] = sum/3

			# top left corner
			ix = 0
			iy = y-1
			sum = (d[xl(ix, iy), xl(ix, iy-1)] +
	               d[xl(ix, iy), xl(ix+1, iy-1)] +
	               d[xl(ix, iy), xl(ix+1, iy)])
			heat[ix, iy] = sum/3

			# top right corner
			ix = x-1
			iy = y-1
			sum = (d[xl(ix, iy), xl(ix-1, iy-1)] +
	               d[xl(ix, iy), xl(ix, iy-1)] +
	               d[xl(ix, iy), xl(ix-1, iy)])
			heat[ix, iy] = sum/3

		if smoothing is not None:
			if smoothing == 0:
				heat = self.smooth_2d(heat,
									  nrow=x,
									  ncol=y,
									  surface=False)
			elif smoothing > 0:
				heat = self.smooth_2d(heat,
									  nrow=x,
									  ncol=y,
									  surface=False,
									  theta=smoothing)
			else:
				sys.exit("compute_heat: bad value for smoothing parameter")

		return heat


	def list_clusters(self, centroids, unique_centroids):
		"""Get the clusters as a list of lists. - CHECK GOOD, not very useful

		Args:
			centroids (matrix): A matrix of the centroid locations in the map.
			unique_centroids (list): A list of unique centroid locations.

		Returns:
			list: A list of clusters associated with each unique centroid.
		"""

		centroids_x_positions = unique_centroids['position_x']
		centroids_y_positions = unique_centroids['position_y']
		cluster_list = []

		for i in range(len(centroids_x_positions)):
			cx = centroids_x_positions[i]
			cy = centroids_y_positions[i]

	    # get the clusters associated with a unique centroid and store it in a list
			cluster_list.append(self.list_from_centroid(cx, cy, centroids))

		return cluster_list

	def list_from_centroid(self, x, y, centroids):
		"""Get all cluster elements associated with one centroid. - CHECK GOOD

		Args:
			x (int): The x position of a centroid.
			y (int): The y position of a centroid.
			centroids (numpy.ndarray): A matrix of the centroid locations in the map.

		Returns:
			list: A list of cluster elements associated with the given centroid.
		"""

		centroid_x = x
		centroid_y = y
		xdim = self.xdim
		ydim = self.ydim

		cluster_list = []
		for xi in range(xdim):
			for yi in range(ydim):
				cx = centroids['centroid_x'][xi, yi]
				cy = centroids['centroid_y'][xi, yi]

				if(cx == centroid_x) and (cy == centroid_y):
					cweight = self.umat[xi, yi]
					cluster_list.append(cweight)

		return cluster_list


	def smooth_2d(self, Y, ind=None, weight_obj=None, grid=None, nrow=64, ncol=64, surface=True, theta=None):
		"""
		Smooths 2D data using a kernel smoother. - CHECK GOOD, internal function, no user-facing aspect

		Args:
			Y (array-like): The input data to be smoothed.
			ind (array-like, optional): The indices of the data to be smoothed. Defaults to None.
			weight_obj (dict, optional): The weight object used for smoothing. Defaults to None.
			grid (dict, optional): The grid object used for smoothing. Defaults to None.
			nrow (int, optional): The number of rows in the grid. Defaults to 64.
			ncol (int, optional): The number of columns in the grid. Defaults to 64.
			surface (bool, optional): Flag indicating whether the data represents a surface. Defaults to True.
			theta (float, optional): The theta value used in the exponential covariance function. Defaults to None.

		Returns:
			array-like: The smoothed data.

		Raises:
			None

		"""

		def exp_cov(x1, x2, theta=2, p=2, distMat=0):
			x1 = x1*(1/theta)
			x2 = x2*(1/theta)
			distMat = euclidean_distances(x1, x2)
			distMat = distMat**p
			return np.exp(-distMat)

		NN = [[1]*ncol] * nrow
		grid = {'x': [i for i in range(nrow)], "y": [i for i in range(ncol)]}

		if weight_obj is None:
			dx = grid['x'][1] - grid['x'][0]
			dy = grid['y'][1] - grid['y'][0]
			m = len(grid['x'])
			n = len(grid['y'])
			M = 2 * m
			N = 2 * n
			xg = []

			for i in range(N):
				for j in range(M):
					xg.extend([[j, i]])

			xg = np.array(xg)

			center = []
			center.append([int(dx * M/2-1), int((dy * N)/2-1)])

			out = exp_cov(xg, np.array(center),theta=theta)
			out = np.transpose(np.reshape(out, (N, M)))
			temp = np.zeros((M, N))
			temp[int(M/2-1)][int(N/2-1)] = 1

			wght = np.fft.fft2(out)/(np.fft.fft2(temp) * M * N)
			weight_obj = {"m": m, "n": n, "N": N, "M": M, "wght": wght}

		temp = np.zeros((weight_obj['M'], weight_obj['N']))
		temp[0:m, 0:n] = Y
		temp2 = np.fft.ifft2(np.fft.fft2(temp) *
							 weight_obj['wght']).real[0:weight_obj['m'],
													  0:weight_obj['n']]

		temp = np.zeros((weight_obj['M'], weight_obj['N']))
		temp[0:m, 0:n] = NN
		temp3 = np.fft.ifft2(np.fft.fft2(temp) *
							 weight_obj['wght']).real[0:weight_obj['m'],
													  0:weight_obj['n']]

		return temp2/temp3

	def starburst(self, explicit=False, smoothing=2, merge_clusters=True, merge_cost=.25):
	
		""" 
		Compute and plot the starburst representation of clusters. - CHECK GOOD
			
		Args:
			explicit (bool, optional): Controls the shape of the connected components. Defaults to False.
			smoothing (int, optional): Controls the smoothing level of the umat. Defaults to 2.
			merge_clusters (bool, optional): A switch that controls if the starburst clusters are merged together. Defaults to True.
			merge_cost (float, optional): A threshold where centroids are close enough to merge. Defaults to .25.
		"""

		umat = self.compute_umat(smoothing=smoothing)
		self.plot_heat(umat,
						explicit=explicit,
						comp=True,
						merge=merge_clusters,
						merge_cost=merge_cost)

	def plot_heat(self, heat, explicit=False, comp=True, merge=False, merge_cost=0.001):
		"""
		Plot the heat map of the given data. - CHECK GOOD

		Args:
			heat (array-like): The data to be plotted.
			explicit (bool, optional): A flag indicating whether the connected components are explicit. Defaults to False.
			comp (bool, optional): A flag indicating whether to plot the connected components. Defaults to True.
			merge (bool, optional): A flag indicating whether to merge the connected components. Defaults to False.
			merge_cost (float, optional): The threshold for merging the connected components. Defaults to 0.001.
		"""

		x = self.xdim
		y = self.ydim
		nobs = self.data_array.shape[0]
		count = np.array([[0]*y]*x)

		# need to make sure the map doesn't have a dimension of 1
		if (x <= 1 or y <= 1):
			sys.exit("plot_heat: map dimensions too small")

		heat_tmp = np.squeeze(np.asarray(heat)).flatten()   	# Convert 2D Array to 1D
		tmp = pd.cut(heat_tmp, bins=100, labels=False)
		tmp = np.reshape(tmp, (-1, y))				# Convert 1D Array to 2D
		
		tmp_1 = np.array(np.transpose(tmp))
		
		fig, ax = plt.subplots(dpi=200)
		plt.rcParams['font.size'] = 8
		ax.pcolor(tmp_1, cmap=plt.cm.YlOrRd)
		
		ax.set_xticks(np.arange(0,x,5)+0.5, minor=False)
		ax.set_yticks(np.arange(0,y,5)+0.5, minor=False)
		plt.xlabel("x")
		plt.ylabel("y")
		ax.set_xticklabels(np.arange(0,x,5), minor=False)
		ax.set_yticklabels(np.arange(0,y,5), minor=False)
		ax.xaxis.set_tick_params(labeltop='on')
		ax.yaxis.set_tick_params(labelright='on')
		ax.xaxis.label.set_fontsize(10)
		ax.yaxis.label.set_fontsize(10)
		ax.set_aspect('equal')
		ax.grid(True)

		# put the connected component lines on the map
		if comp:
			
			# find the centroid for each node on the map
			centroids = self.compute_centroids(explicit)
			if merge:
				# find the unique centroids for the nodes on the map
				centroids = self.merge_similar_centroids(centroids, merge_cost)

			unique_centroids = self.get_unique_centroids(centroids)
			print("Unique centroids : ", unique_centroids)

			unique_centroids['position_x'] = [x+0.5 for x in unique_centroids['position_x']]
			unique_centroids['position_y'] = [y+0.5 for y in unique_centroids['position_y']]

			plt.scatter(unique_centroids['position_x'],unique_centroids['position_y'], color='red', s=10)

			# connect each node to its centroid
			for ix in range(x):
				for iy in range(y):
					cx = centroids['centroid_x'][ix, iy]
					cy = centroids['centroid_y'][ix, iy]
					plt.plot([ix+0.5, cx+0.5],
	                         [iy+0.5, cy+0.5],
	                         color='grey',
	                         linestyle='-',
	                         linewidth=1.0)

		# put the labels on the map if available
		if not (self.labels is None) and (len(self.labels) != 0):
			self.map_data_to_lattice() # obtain the projection_1d array

			# count the labels in each map cell
			for i in range(nobs):

				nix = self.projection_1d[i]
				c = self.coordinate(np.reshape(nix,(1,1)), self.xdim) # NOTE: slow code
				# print(c)
				ix = int(c[0,0])
				iy = int(c[0,1])

				count[ix-1, iy-1] = count[ix-1, iy-1]+1

			for i in range(nobs):

				c = self.coordinate(np.reshape(self.projection_1d[i],(1,1)), self.xdim) # NOTE: slow code
				ix = int(c[0,0])
				iy = int(c[0,1])

				# we only print one label per cell
				if count[ix-1, iy-1] > 0:

					count[ix-1, iy-1] = 0
					ix = ix - .5
					iy = iy - .5
					l = self.labels[i]
					plt.text(ix+1, iy+1, l)

		plt.show()

	## NOT WORKING; PORTED FROM POPSOM --------------------------------------------------------------

	# def marginal(self, marginal):
	# 	""" marginal -- plot that shows the marginal probability distribution of the lattice and data

	# 	 	parameters:
	# 	 	- marginal is the name of a training data frame dimension or index
	# 	"""
		
	# 	# check if the second argument is of type character
	# 	if type(marginal) == str and marginal in self.features_names:

	# 		f_ind = self.features_names.index(marginal)
	# 		f_name = marginal
	# 		train = self.data_array[:, f_ind]
	# 		lattice_of_1feature = self.lattice[:, f_ind]
	# 		plt.ylabel('Density')
	# 		plt.xlabel(f_name)
	# 		sns.kdeplot(np.ravel(train),
	# 			        label="training data",
	# 					shade=True,
	# 					color="b")
	# 		sns.kdeplot(lattice_of_1feature, label="lattice", shade=True, color="r")
	# 		plt.legend(fontsize=15)
	# 		plt.show()

	# 	elif (type(marginal) == int and marginal < len(self.features_names) and marginal >= 0):

	# 		f_ind = marginal
	# 		f_name = self.features_names[marginal]
	# 		train = self.data_array[:, f_ind]
	# 		lattice_of_1feature = self.lattice[:, f_ind]
	# 		plt.ylabel('Density')
	# 		plt.xlabel(f_name)
	# 		sns.kdeplot(np.ravel(train),
	# 					label="training data",
	# 					shade=True,
	# 					color="b")
	# 		sns.kdeplot(lattice_of_1feature, label="lattice", shade=True, color="r")
	# 		plt.legend(fontsize=15)
	# 		plt.show()

	# 	else:
	# 		sys.exit("marginal: second argument is not the name of a training \
	# 					data frame dimension or index")

	# def significance(self, graphics=False, feature_labels=False):
	# 	""" significance -- compute the relative significance of each feature and plot it
		
	# 		parameters:
	# 		- graphics - a switch that controls whether a plot is generated or not
	# 		- feature_labels - a switch to allow the plotting of feature names vs feature indices
			
	# 		return value:
	# 		- a vector containing the significance for each feature  
	# 	"""

	# 	nfeatures = self.data_array.shape[1]

	#     # Compute the variance of each feature on the map
	# 	var_v = [randint(1, 1) for _ in range(nfeatures)]

	# 	for i in range(nfeatures):
	# 		var_v[i] = np.var(self.data_array[:, i])

	#     # we use the variance of a feature as likelihood of
	#     # being an important feature, compute the Bayesian
	#     # probability of significance using uniform priors

	# 	var_sum = np.sum(var_v)
	# 	prob_v = var_v/var_sum

	#     # plot the significance
	# 	if graphics:
	# 		y = max(prob_v)

	# 		plt.axis([0, nfeatures+1, 0, y])

	# 		x = np.arange(1, nfeatures+1)
	# 		tag = self.features_names

	# 		plt.xticks(x, tag)
	# 		plt.yticks = np.linspace(0, y, 5)

	# 		i = 1
	# 		for xc in prob_v:
	# 			plt.axvline(x=i, ymin=0, ymax=xc)
	# 			i += 1

	# 		plt.xlabel('Features')
	# 		plt.ylabel('Significance')
	# 		plt.show()
	# 	else:
	# 		return prob_v
	
	# def bootstrap(self, conf_int, k, sample_acc_v):
	# 	""" bootstrap -- compute the topographic accuracies for the given confidence interval """

	# 	ix = int(100 - conf_int*100)
	# 	bn = 200

	# 	bootstrap_acc_v = [np.sum(sample_acc_v)/k]

	# 	for i in range(2, bn+1):

	# 		bs_v = np.array([randint(1, k) for _ in range(k)])-1
	# 		a = np.sum(list(np.array(sample_acc_v)[list(bs_v)]))/k
	# 		bootstrap_acc_v.append(a)

	# 	bootstrap_acc_sort_v = np.sort(bootstrap_acc_v)

	# 	lo_val = bootstrap_acc_sort_v[ix-1]
	# 	hi_val = bootstrap_acc_sort_v[bn-ix-1]

	# 	return {'lo': lo_val, 'hi': hi_val}	

	# def convergence(self, conf_int=.95, k=50, verb=False, ks=False):
	# 	""" 
	# 	Calculates the convergence index of a map.
		
	# 	Args:
	# 		conf_int (float, optional): The confidence interval of the quality assessment. Defaults to 0.95.
	# 		k (int, optional): The number of samples used for the estimated topographic accuracy computation. Defaults to 50.
	# 		verb (bool, optional): If True, reports the two convergence components separately. Otherwise, reports the linear combination of the two. Defaults to False.
	# 		ks (bool, optional): A switch. If True, uses the ks-test. If False, uses standard var and means test. Defaults to False.
		
	# 	Returns:
	# 		float or dict: The convergence index. If verb is True, returns a dictionary with the convergence components ('embed' and 'topo'). If verb is False, returns the linear combination of the two components.
	# 	"""

	# 	if ks:
	# 		embed = self.embed_ks(conf_int, verb=False)
	# 	else:
	# 		embed = self.embed_vm(conf_int, verb=False)

	# 	topo_ = self.topo(k, conf_int, verb=False, interval=False)

	# 	if verb:
	# 		return {"embed": embed, "topo": topo_}
	# 	else:
	# 		return (0.5*embed + 0.5*topo_)	

	# def accuracy(self, sample, data_ix): # this is topographical error
	# 	""" accuracy -- the topographic accuracy of a single sample is 1 is the best matching unit
	# 	             	and the second best matching unit are are neighbors otherwise it is 0
	# 	"""

	# 	o = self.best_match(self.lattice, np.reshape(sample, (1,len(sample))), full=True)
	# 	best_ix = o[0]
	# 	second_best_ix = o[1]

	# 	# sanity check
	# 	coord = self.coordinate(np.reshape(best_ix,(1,1)), self.xdim)
	# 	coord_x = coord[0,0]
	# 	coord_y = coord[0,1]

	# 	map_ix = self.projection_1d[data_ix-1] # NOTE: this might not work because of dependency on projection_1d
	# 	coord = self.coordinate(np.reshape(map_ix,(1,1)), self.xdim)
	# 	map_x = coord[0,0]
	# 	map_y = coord[0,1]

	# 	if (coord_x != map_x or coord_y != map_y or best_ix != map_ix):
	# 		print("Error: best_ix: ", best_ix, " map_ix: ", map_ix, "\n")

	# 	# determine if the best and second best are neighbors on the map
	# 	best_xy = self.coordinate(np.reshape(best_ix,(1,1)), self.xdim)
	# 	second_best_xy = self.coordinate(np.reshape(second_best_ix,(1,1)), self.xdim)
	# 	diff_map = best_xy[0,:] - second_best_xy[0,:]
	# 	diff_map_sq = diff_map * diff_map
	# 	sum_map = np.sum(diff_map_sq)
	# 	dist_map = np.sqrt(sum_map)

	# 	# it is a neighbor if the distance on the map
	# 	# between the bmu and 2bmu is less than 2,   should be 1 or 1.414
	# 	if dist_map < 2:
	# 		return 1
	# 	else:
	# 		return 0
		
	# def embed(self, conf_int=.95, verb=False, ks=False):
	# 	""" embed -- evaluate the embedding of a map using the F-test and
	# 	             a Bayesian estimate of the variance in the training data.
		
	# 		parameters:
	# 		- conf_int - the confidence interval of the convergence test (default 95%)
	# 		- verb - switch that governs the return value false: single convergence value
	# 		  		 is returned, true: a vector of individual feature congences is returned.
			
	# 		- return value:
	# 		- return is the cembedding of the map (variance captured by the map so far)

	# 		Hint: 
	# 			  the embedding index is the variance of the training data captured by the map;
	# 		      maps with convergence of less than 90% are typically not trustworthy.  Of course,
	# 		      the precise cut-off depends on the noise level in your training data.
	# 	"""

	# 	if ks:
	# 		return self.embed_ks(conf_int, verb)
	# 	else:
	# 		return self.embed_vm(conf_int, verb)

	# def embed_ks(self, conf_int=0.95, verb=False):
		
	# 	"""
	# 	Computes the variance captured by the map using the Kolmogorov-Smirnov test.

	# 	Args:
	# 		conf_int (float, optional): Confidence interval for the Kolmogorov-Smirnov test. Defaults to 0.95.
	# 		verb (bool, optional): If True, returns the probability vector. If False, returns the variance captured. Defaults to False.

	# 	Returns:
	# 		float or list: The variance captured by the converged features if verb is False. The probability vector if verb is True.
	# 	"""

	# 	nfeatures = self.lattice.shape[1]

	# 	# use the Kolmogorov-Smirnov Test to test whether the lattice and training data appear to come from the same distribution
	# 	ks_vector = []
	# 	for i in range(nfeatures):
	# 		ks_vector.append(stats.mstats.ks_2samp(self.lattice[:, i], self.data_array[:, i]))

	# 	prob_v = self.significance(graphics=False)
	# 	var_sum = 0

	# 	# compute the variance captured by the map
	# 	for i in range(nfeatures):

	# 		# the second entry contains the p-value
	# 		if ks_vector[i][1] > (1 - conf_int):
	# 			var_sum = var_sum + prob_v[i]
	# 		else:
	# 			# not converged - zero out the probability
	# 			prob_v[i] = 0

	# 	# return the variance captured by converged features
	# 	if verb:
	# 		return prob_v
	# 	else:
	# 		return var_sum

	# def embed_vm(self, conf_int=.95, verb=False):
	# 	"""
	# 	Embeds the training data into the SOM using variance and mean tests.

	# 	Args:
	# 		conf_int (float, optional): Confidence interval for the tests. Defaults to 0.95.
	# 		verb (bool, optional): Verbosity flag. If True, returns the probability vector. 
	# 			If False, returns the variance captured by converged features. Defaults to False.

	# 	Returns:
	# 		float or list: If verb is True, returns a list of probabilities for each feature. 
	# 			If verb is False, returns the variance captured by converged features.

	# 	Raises:
	# 		SystemExit: If the number of features in the data frames is not equal.

	# 	"""

	# 	def df_var_test(df1: np.ndarray, df2: np.ndarray, conf=.95):

	# 		if df1.shape[1] != df2.shape[1]:
	# 			sys.exit("df_var_test: cannot compare variances of data frames")

	# 		# init our working arrays
	# 		var_ratio_v = [randint(1, 1) for _ in range(df1.shape[1])]
	# 		var_confintlo_v = [randint(1, 1) for _ in range(df1.shape[1])]
	# 		var_confinthi_v = [randint(1, 1) for _ in range(df1.shape[1])]

	# 		def var_test(x, y, ratio=1, conf_level=0.95):

	# 			DF_x = len(x) - 1
	# 			DF_y = len(y) - 1
	# 			V_x = np.var(x)
	# 			V_y = np.var(y)

	# 			ESTIMATE = V_x / V_y

	# 			BETA = (1 - conf_level) / 2
	# 			CINT = [ESTIMATE / f.ppf(1 - BETA, DF_x, DF_y),
	# 					ESTIMATE / f.ppf(BETA, DF_x, DF_y)]

	# 			return {"estimate": ESTIMATE, "conf_int": CINT}

	# 	    # compute the F-test on each feature in our populations
	# 		for i in range(df1.shape[1]):

	# 			t = var_test(df1[:, i], df2[:, i], conf_level=conf)
	# 			var_ratio_v[i] = t['estimate']
	# 			var_confintlo_v[i] = t['conf_int'][0]
	# 			var_confinthi_v[i] = t['conf_int'][1]

	# 		# return a list with the ratios and conf intervals for each feature
	# 		return {"ratio": var_ratio_v,
	# 				"conf_int_lo": var_confintlo_v,
	# 				"conf_int_hi": var_confinthi_v}

	# 	def df_mean_test(df1: np.ndarray, df2: np.ndarray, conf=0.95):

	# 		if df1.shape[1] != df2.shape[1]:
	# 			sys.exit("df_mean_test: cannot compare means of data frames")

	# 		# init our working arrays
	# 		mean_diff_v = [randint(1, 1) for _ in range(df1.shape[1])]
	# 		mean_confintlo_v = [randint(1, 1) for _ in range(df1.shape[1])]
	# 		mean_confinthi_v = [randint(1, 1) for _ in range(df1.shape[1])]

	# 		def t_test(x, y, conf_level=0.95):
	# 			estimate_x = np.mean(x)
	# 			estimate_y = np.mean(y)
	# 			cm = sms.CompareMeans(sms.DescrStatsW(x), sms.DescrStatsW(y))
	# 			conf_int_lo = cm.tconfint_diff(alpha=1-conf_level, usevar='unequal')[0]
	# 			conf_int_hi = cm.tconfint_diff(alpha=1-conf_level, usevar='unequal')[1]

	# 			return {"estimate": [estimate_x, estimate_y],
	# 					"conf_int": [conf_int_lo, conf_int_hi]}

	# 		# compute the F-test on each feature in our populations
	# 		for i in range(df1.shape[1]):
	# 			t = t_test(x=df1[:, i], y=df2[:, i], conf_level=conf)
	# 			mean_diff_v[i] = t['estimate'][0] - t['estimate'][1]
	# 			mean_confintlo_v[i] = t['conf_int'][0]
	# 			mean_confinthi_v[i] = t['conf_int'][1]

	# 		# return a list with the ratios and conf intervals for each feature
	# 		return {"diff": mean_diff_v,
	# 				"conf_int_lo": mean_confintlo_v,
	# 				"conf_int_hi": mean_confinthi_v}
	# 	# do the F-test on a pair of datasets
	# 	vl = df_var_test(self.lattice, self.data_array, conf_int)

	# 	# do the t-test on a pair of datasets
	# 	ml = df_mean_test(self.lattice, self.data_array, conf=conf_int)

	# 	# compute the variance captured by the map --
	# 	# but only if the means have converged as well.
	# 	nfeatures = self.lattice.shape[1]
	# 	prob_v = self.significance(graphics=False)
	# 	var_sum = 0

	# 	for i in range(nfeatures):

	# 		if (vl['conf_int_lo'][i] <= 1.0 and vl['conf_int_hi'][i] >= 1.0 and
	# 			ml['conf_int_lo'][i] <= 0.0 and ml['conf_int_hi'][i] >= 0.0):

	# 			var_sum = var_sum + prob_v[i]
	# 		else:
	# 			# not converged - zero out the probability
	# 			prob_v[i] = 0

	# 	# return the variance captured by converged features
	# 	if verb:
	# 		return prob_v
	# 	else:
	# 		return var_sum

	# def topo(self, k=50, conf_int=.95, verb=False, interval=True):
	# 	""" topo -- measure the topographic accuracy of the map using sampling
		
	# 		parameters:
	# 		- k - the number of samples used for the accuracy computation
	# 		- conf_int - the confidence interval of the accuracy test (default 95%)
	# 		- verb - switch that governs the return value, false: single accuracy value
	# 		  		 is returned, true: a vector of individual feature accuracies is returned.
	# 		- interval - a switch that controls whether the confidence interval is computed.
			
	# 		- return value is the estimated topographic accuracy
	# 	"""

	# 	if (k > self.data_array.shape[0]):
	# 		sys.exit("topo: sample larger than training data.")

	# 	data_sample_ix = np.array([randint(1, self.data_array.shape[0]) for _ in range(k)])

	# 	# compute the sum topographic accuracy - the accuracy of a single sample
	# 	# is 1 if the best matching unit is a neighbor otherwise it is 0
	# 	acc_v = np.zeros(k)

	# 	# NOTE: this line used dataframe, not sure if this conversion to numpy array is correct
	# 	for i in range(k):
	# 		acc_v[i] = self.accuracy(self.data_array[data_sample_ix[i]-1], data_sample_ix[i])

	# 	# compute the confidence interval values using the bootstrap
	# 	if interval:
	# 		bval = self.bootstrap(conf_int, self.data_array, k, acc_v)

	# 	# the sum topographic accuracy is scaled by the number of samples -
	# 	# estimated
	# 	# topographic accuracy
	# 	if verb:
	# 		return acc_v
	# 	else:
	# 		val = np.sum(acc_v)/k
	# 		if interval:
	# 			return {'val': val, 'lo': bval['lo'], 'hi': bval['hi']}
	# 		else:
	# 			return val
