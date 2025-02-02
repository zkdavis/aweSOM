---
title: '`aweSOM`: a CPU/GPU-accelerated Self-organizing Map and Statistically Combined Ensemble Framework for Machine-learning Clustering Analysis'
tags:
  - Python
  - astronomy
  - plasma
authors:
  - name: Trung Ha
    orcid: 0000-0001-6600-2517
    corresponding: true
    affiliation: "1, 2, 3"
  - name: Joonas Nättilä
    orcid: 0000-0002-3226-4575
    affiliation: "4, 5, 1"
  - name: Jordy Davelaar
    orcid: 0000-0002-2685-2434
    affiliation: "6, 7, 1, 5"
affiliations:
 - name: Department of Astronomy, University of Massachusetts-Amherst, Amherst, MA 01003, USA
   index: 1
 - name: Center for Computational Astrophysics, Flatiron Institute, 162 Fifth Avenue, New York, NY 10010, USA
   index: 2
 - name: Department of Physics, University of North Texas, Denton, TX 76203, USA
   index: 3
 - name: Department of Physics, University of Helsinki, P.O. Box 64, University of Helsinki, FI-00014, Finland
   index: 4
 - name: Physics Department and Columbia Astrophysics Laboratory, Columbia University, 538 West 120th Street, New York, NY 10027, USA
   index: 5
 - name: Department of Astrophysical Sciences, Peyton Hall, Princeton University, Princeton, NJ 08544, USA
   index: 6
 - name: NASA Hubble Fellowship Program, Einstein Fellow
   index: 7
date: 08 October 2024
bibliography: paper.bib

# Optional fields if submitting to a AAS journal too, see this blog post:
# https://blog.joss.theoj.org/2018/12/a-new-collaboration-with-aas-publishing
# aas-doi: 10.3847/xxxxx <- update this with the DOI from AAS once you know it.
# aas-journal: Astrophysical Journal <- The name of the AAS journal.
---

# Summary

We introduce `aweSOM`, an open-source Python package for machine learning (ML) clustering and classification, using a Self-organizing Maps [SOM, @kohonen1990] algorithm that incorporates CPU/GPU acceleration to accommodate large ($N > 10^6$, where $N$ is the number of data points), multidimensional datasets. `aweSOM` consists of two main modules, one that handles the initialization and training of the SOM, and another that stacks the results of multiple SOM realizations to obtain more statistically robust clusters.

Existing Python-based SOM implementations (e.g., `POPSOM`, @yuan2018; `MiniSom`, @minisom; `sklearn-som`) primarily serve as proof-of-concept demonstrations, optimized for smaller datasets, but lacking scalability for large, multidimensional data.
`aweSOM` provides a solution for this gap in capability, with good performance scaling up to $\sim 10^8$ individual points, and capable of utilizing multiple features per point. We compare the code performance against the legacy implementations it is based on, and find a $10 - 100 \times$ speed up, as well as significantly improved memory efficiency, due to several built-in optimizations.

As a companion to this paper, @ha2024 demonstrates the capabilities of `aweSOM` in analyzing the physics of plasma turbulence. Detailed instructions on how to install, test, and replicate the results of the paper are available in the online [documentation](https://awesom.readthedocs.io/en/latest/). Also included in the documentation is an example of applying `aweSOM` to the Iris dataset [@iris53].

# Statement of need

## The self-organizing map algorithm

A SOM algorithm is an unsupervised ML technique that excels at dimensionality reduction, clustering, and classification tasks.
It consists of a 2-dimensional (2D) lattice of nodes. Each node contains a weight vector that matches the dimensionality of the input data. A SOM performs clustering by adapting the weight vectors of nodes, progressively reshaping the lattice’s topology to match the intrinsic clustering of the input data.
In this manner, a SOM lattice can capture multidimensional correlations in the input data.

SOM is commonly used in various real-world applications, such as in the financial sector [e.g., @Alshantti2021; @Pei2023], in environmental surveys [e.g., @Alvarez2008; @Li2020], in medical technology [e.g., @Hautaniemi2003; @Kawaguchi2024], among others. `aweSOM` is originally developed to be used in analyzing astrophysical simulations, but can be applied to a wide variety of real-world data.

### `POPSOM`

We base the SOM module of `aweSOM` on `POPSOM` [@yuan2018; @hamel2019], a R-based SOM model. `POPSOM` was developed as a single-threaded, stochastic training algorithm with built-in visualization capabilities. However, due to its single-threaded nature, the algorithm does not scale well with large datasets. When $N \gtrsim 10^6$, `POPSOM` is often unable to complete the training process as the dimensionality of the input data increases due to its much higher memory usage. As an example, we generated a mock dataset with $N = 10^6$ and $F = 6$ dimensions, then trained it on a lattice of $X = 63$, and $Y = 32$, where $X, Y$ are the dimensions of the lattice, using one Intel Icelake node with 64 cores and 1 TB memory. `POPSOM` completed the training in $\approx 2200$ s and consumed $\approx 600$ GB of system memory at its peak.

### Rewriting `POPSOM` into `aweSOM`

To combat the long training time and excessive memory usage, we rewrite `POPSOM` with multiple optimizations/parallelizations. 
We replaced legacy code with modern `NumPy` functions for updating the lattice (a 3D array) and eliminated the use of `pandas` DataFrames [@pandas], which consume significantly more memory. The weight vector modifications in the DataFrame were also less efficient compared to the NumPy arrays used in aweSOM.
Furthermore, for the steps where parallelization could be leveraged (such as when the cluster labels are mapped to the lattice, then to the input data), we integrate `Numba` [@numba] to take advantage of its Just-In-Time (JIT) compiler and simple parallelization of loops. In the same example as above, `aweSOM` took $\approx 200$ s and consumed $\approx 450$ MB of memory to complete the training and clustering. In addition to the $\sim 10 \times$ speedup, `aweSOM` is also $\sim 10^3 \times$ more memory-efficient.

The left hand side of \autoref{fig:sce_scaling} shows a graph of the performance between `aweSOM` and the legacy `POPSOM` implementation over a range of $N$ and $F$, performed on one Intel Icelake compute node with 64 CPU cores and 1 TB memory. 
While `POPSOM` initially performs slightly faster than `aweSOM` for $N \lesssim 10^4$, this changes when $N$ exceeds $5 \times 10^5$, after that `aweSOM` consistently outperforms `POPSOM` by approximately a factor of $10$.
Critically, `POPSOM` fails to complete its clusters mapping for $N \gtrsim 10^6, F > 4$ because the memory buffer of the test node was exceeded.

## The statistically combined ensemble method

The statistically combined ensemble (SCE) method was developed by @bussov2021 to stack the result of multiple independent clustering realizations into a statically significant set of clusters. This method represents a form of ensemble learning. Additionally, SCE can also be used independently from the base SOM algorithm, and is compatible with any general unsupervised classification algorithm. 

### The legacy SCE implementation

In its original version, the SCE was saved as a nested dictionary of boolean arrays, each of which contains the spatial similarity index $g$ between cluster $C$ and cluster $C'$. The total number of operations scales as $N_{C}^R$, where $N_C$ is the number of clusters in each realization, and $R$ is the number of realizations. For example, in our use case involving plasma simulation data [@ha2024], each SOM realization produces on average 7 clusters, and the SCE analysis incorporates 36 realizations, resulting in approximately $7^{36} \sim 10^{30}$ array-to-array comparisons.

### Integrating SCE into `aweSOM` with `JAX`

To mitigate this bottleneck, we rewrite the legacy SCE code with `JAX` [@jax] to significantly enhance the performance of array-to-array comparisons (which are matrix multiplications) by leveraging the GPU's parallel-computing advantage over the CPU. We implement this optimization by replacing the original nested dictionaries with data arrays. Then, every instance of matrix operation using `NumPy` is converted to `jax.numpy`. Additionally, we implement internal checks such that the SCE code automatically reverts to `NumPy` if GPU-accelerated `JAX` is not available.

Similar to the SOM implementation, the SCE implementation in `aweSOM` demonstrates excellent scalability as the number of data points increases.
The right hand side of \autoref{fig:sce_scaling} shows a graph of the performance between the two implementations given $R = 20$. At $N < 5 \times 10^4$, the legacy code is faster due to the overhead from loading `JAX` and the JIT compiler. However, `aweSOM` quickly exceeds the performance of the legacy code, and begins to approach its maximum speed-up of $\sim 100 \times$ at $N \gtrsim 10^7$ (performed on one NVIDIA A100-40GB GPU). On the other hand, when running on CPU-only with `NumPy`, `aweSOM` consistently shows a $2 \times$ speed improvement over the legacy code. Altogether, it is best to use `aweSOM` with `Numpy` when $N \lesssim 10^5$, and with `JAX` when $N \gtrsim 10^5$.

![Performance scaling for `aweSOM` vs. the legacy SOM (left) and SCE (right) implementation. The top panels show the time for each implementation to complete analysis of $N$ number of data points. The dotted lines shows linear extrapolations from the data in order to estimate the speedup. The bottom panels show the ratio between the time taken by the legacy code divided by the time taken by `aweSOM`. In the SOM analysis, we consider a dataset with $F = 6$ and $F = 10$ dimensions. In the SCE analysis, we test the scaling of both a GPU-accelerated implementation (with `JAX`) and a CPU-only implementation (with `NumPy`). \label{fig:sce_scaling}](joss_scaling.pdf)

# Acknowledgements

The authors would like to thank Kaze Wong for the valuable input in setting up `JAX` for the SCE analysis. The authors would also like to thank Shirley Ho and Lorenzo Sironi for the useful discussions.
TH acknowledges support from a pre-doctoral program at the Center for Computational Astrophysics, which is part of the Flatiron Institute. JN is supported by an ERC grant (ILLUMINATOR, 101114623). JD is supported by NASA through the NASA Hubble Fellowship grant HST-HF2-51552.001-A, awarded by the Space Telescope Science Institute, which is operated by the Association of Universities for Research in Astronomy, Incorporated, under NASA contract NAS5-26555.
`aweSOM` was developed and primarily run at facilities supported by the Scientific Computing Core at the Flatiron Institute. Research at the Flatiron Institute is supported by the Simons Foundation.

# References
