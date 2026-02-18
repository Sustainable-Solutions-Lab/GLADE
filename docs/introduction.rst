.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Introduction
============

Overview
--------

``food-opt`` is a global food systems optimization model designed to study trade-offs between positive health outcomes and desirable environmental outcomes. It can be used to answer questions like: *How could we feed the world's population while minimizing greenhouse gas emissions and diet-related disease burden? What are the trade-offs and synergies between environmental sustainability and food security?*

The model represents the global food system as a network of material flows—from land and water inputs, through crop production, livestock systems and trade, to processed foods and human consumption. It uses **linear programming** (a mathematical optimization technique) to find the combination of production, conversion, trade and consumption choices that best achieves specified objectives while respecting physical constraints like available land, water, and crop yields.

.. admonition:: Technical Implementation
   :class: note

   ``food-opt`` is built on `PyPSA <https://pypsa.org/>`_ (Python for Power System Analysis), an open-source framework originally designed for energy system modeling. We adapt PyPSA's flexible network structure to represent food flows instead of energy flows. PyPSA has built-in functionality helping translating a component and flow-based model to a set of equation representing a linear program. The workflow is orchestrated by `Snakemake <https://snakemake.readthedocs.io/>`_, which ensures reproducibility by automatically tracking dependencies between processing steps and only re-running what's necessary when inputs change.

Key Objectives
~~~~~~~~~~~~~~

The model balances multiple objectives:

* **Environmental sustainability**: Minimize greenhouse gas emissions (CO₂, CH₄, N₂O), as well as possibly land use change, nitrogen pollution, and water use
* **Health outcomes**: Minimize disease burden from dietary risk factors

These objectives are co-optimized for while operating within biophysical limits on crop yields, land availability, and irrigation capacity as well as satisfying constraints on nutritional adequacy in terms of macronutrients.

Model Structure
~~~~~~~~~~~~~~~

The model represents material flows through the global food system, from primary inputs (land, water, synthetic fertilizer) through crop production, animal feed systems, trade, and food processing to final outputs including human nutrition, biomass exports to the energy sector, and environmental emissions.

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/model_topology.png
   :width: 100%
   :alt: Model topology showing high-level material flows

   High-level topology of the food systems model, showing aggregated material flows between major system components. Node colors indicate functional categories: blue-gray for inputs, green for food products, orange for biomass exports to the energy sector, dark green for nutrients, and gray for emissions.

Key Features
------------

Food System Coverage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Crop production**: more than 60 different crops with spatially-explicit yield potentials
* **Livestock systems**: Multiple production systems (grazing vs. feed-based) for meat and dairy
* **Food processing and trade**: Accounting for waste losses, trading frictions and more
* **Nutritional assessment**: Mapping to food group-based dietary risk factors and health outcomes

Environmental Impact Assessment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* Greenhouse gas emissions from production, land use change, and nitrogen fertilization
* Land use change impacts with spatially-explicit carbon storage estimates
* Water use constraints based on irrigation infrastructure and basin-level availability
* Nitrogen pollution from fertilizer application

Health and Nutrition
~~~~~~~~~~~~~~~~~~~~~

* Macronutrient constraints to ensure basic nutritional adequacy
* Integration with Global Burden of Disease dietary risk factors
* Population-level health impact assessment in terms of years of life lost
* Health valuation via a configurable value-per-YLL constant

Global Extent & Flexible Spatial Resolution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* Geophysical input data at high-resolution gridcell level (0.05° × 0.05°)
* Optimization at configurable sub-national regional scale
* Global coverage with detailed country and regional analysis

.. figure:: https://github.com/Sustainable-Solutions-Lab/food-opt/releases/download/doc-figures/intro_global_coverage.png
   :width: 100%
   :alt: Global model coverage map

   Global model coverage showing optimization regions. The model divides the world into sub-national regions (here 250 regions) that balance spatial detail with computational tractability. Each colored polygon represents an optimization region created by clustering administrative units.

Getting Started
---------------

This section guides you through setting up ``food-opt`` on your computer. Familiarity with command-line tools and Python projects is an advantage, but the setup process is straightforward and most dependencies are handled automatically.

.. note::

   **Disk space requirements**: The model downloads several large datasets (GAEZ crop yields, administrative boundaries, land cover, etc.); together with software dependencies, intermediate and final results, you should expect ``food-opt`` to take up some 30GB of disk space or more.

   **Runtime estimates**: The first model run may take about an hour (depending on your internet speed and computer performance) as a large amount of data will be downloaded and pre-processed. Subsequent runs, where only the core model needs to be built and solved, will only take a few minutes.

What You'll Need
~~~~~~~~~~~~~~~~

**To install manually** (before running ``food-opt``):

* `Git <https://git-scm.com/>`_ — version control system for downloading the code
* `pixi <https://pixi.sh/>`_ — cross-platform package manager that handles all other dependencies

**Installed automatically by pixi**:

* Python 3.13+
* Snakemake (workflow automation)
* PyPSA, pandas, geopandas, and all other Python packages
* HiGHS linear programming solver (open-source; Gurobi optional for faster solving)

**Requires manual download or registration** (due to licensing terms):

* Three health/dietary datasets from IHME and Tufts University (free registration required)
* Copernicus Climate Data Store API credentials (free registration required)
* Optionally: USDA FoodData Central API key (for refreshing nutritional data)

Installation
~~~~~~~~~~~~

1. **Clone the repository**:

   .. code-block:: bash

      git clone https://github.com/Sustainable-Solutions-Lab/food-opt.git
      cd food-opt

2. **Install dependencies** (this also installs Python and Snakemake):

   .. code-block:: bash

      pixi install

   This may take a few minutes on first run as pixi downloads and configures the environment.

   .. note::

      **Older Linux systems (e.g. compute clusters)**: Pixi assumes a minimum glibc
      version of 2.28 by default. If your system has an older glibc (check with
      ``ldd --version``), dependency resolution may fail. To fix this, add a
      ``[system-requirements]`` section to ``pixi.toml`` specifying your system's
      glibc version:

      .. code-block:: toml

         [system-requirements]
         libc = { family = "glibc", version = "2.17" }

      Replace ``"2.17"`` with whatever version ``ldd --version`` reports on your
      system. After adding this, run ``pixi update`` to re-resolve all dependencies
      against the lower glibc constraint, then ``pixi install`` as normal.

3. **Set up API credentials** for external data services:

   Copy the secrets template and fill in your credentials:

   .. code-block:: bash

      cp config/secrets.yaml.example config/secrets.yaml

   Then edit ``config/secrets.yaml`` with your credentials:

   * **ECMWF Climate Data Store** (required for land cover data):

     1. Register at https://cds.climate.copernicus.eu/user/register
     2. Accept the dataset license at https://cds.climate.copernicus.eu/datasets/satellite-land-cover
     3. Copy your API key from your profile page

   * **USDA FoodData Central** (optional; repository includes pre-fetched data):

     Get a free API key at https://fdc.nal.usda.gov/api-key-signup

   Alternatively, you can set environment variables instead of using the secrets file:

   .. code-block:: bash

      export ECMWF_DATASTORES_URL="https://cds.climate.copernicus.eu/api"
      export ECMWF_DATASTORES_KEY="your-uid:your-api-key"
      export USDA_API_KEY="your-usda-api-key"

4. **Download required datasets manually**:

   Three datasets cannot be downloaded automatically due to licensing terms. See the :ref:`manual-download-checklist` in the Data Sources documentation for step-by-step instructions:

   * IHME GBD 2023 mortality rates (requires free IHME account)
   * IHME GBD 2019 relative risk data (requires free IHME account)
   * Global Dietary Database (requires free GDD account)

   Place downloaded files in ``data/manually_downloaded/`` as described in the checklist.

5. **Verify your setup** with a test run:

   .. code-block:: bash

      tools/smk -j4 --configfile config/toy.yaml -n

   The ``-n`` flag performs a "dry run" that shows what would be executed without actually running anything. If this completes without errors, your setup is ready.

Quick Start
-----------

Running Your First Model
~~~~~~~~~~~~~~~~~~~~~~~~~

Once installation is complete, you can run your first model. All commands below should be run from a terminal (command prompt) in the ``food-opt`` directory.

**Option 1: Run the validation configuration**

The repository includes a pre-configured validation scenario that you can run immediately:

.. code-block:: bash

   tools/smk -j4 --configfile config/validation.yaml

This runs a scenario designed to validate model behavior against observed data.
See :doc:`validation` for details on how validation works and what the results
reveal.

**Option 2: Create your own scenario**

Configuration files act as overrides to the default configuration (``config/default.yaml``). You only need to specify the settings you want to change; all other settings are inherited automatically.

1. Copy the example configuration:

   .. code-block:: bash

      cp config/example.yaml config/my_scenario.yaml

2. Edit ``config/my_scenario.yaml`` and change the ``name`` field to match your scenario
   (for example ``name: "my_scenario"``). Optionally add any configuration overrides.
   See ``config/example.yaml`` for documented examples of common overrides.

3. Run the workflow with your scenario file:

   .. code-block:: bash

      tools/smk -j4 --configfile config/my_scenario.yaml

   The ``-j4`` flag tells Snakemake to run up to 4 tasks in parallel. Adjust this number based on your computer's CPU cores.

Either option will:

1. Download required global datasets (GAEZ, GADM, UN population, etc.)
2. Process and harmonize spatial data for the configured countries
3. Build the linear programming model
4. Solve the optimization problem
5. Generate summary statistics and visualizations

Results will be saved under ``results/my_scenario/``.

Understanding the Workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The Snakemake workflow is organized into stages:

* **Data preparation**: Population, regions, resource classes, crop yields
* **Model building**: Assemble PyPSA network with all constraints
* **Solving**: Run the linear program with configured solver
* **Visualization**: Generate maps, plots, and CSV exports

You can target individual stages by specifying the output file. For example, to only build the model without solving:

.. code-block:: bash

   tools/smk -j4 --configfile config/my_scenario.yaml -- results/my_scenario/build/model_scen-default.nc

Or to just prepare regional aggregation:

.. code-block:: bash

   tools/smk -j4 --configfile config/my_scenario.yaml -- processing/my_scenario/regions.geojson

.. tip::

   The ``--`` separator is used to clearly separate command-line options (like ``-j4`` or ``--configfile``) from the target file or rule name. This prevents Snakemake from misinterpreting target paths as options.

See :doc:`workflow` for detailed information on the workflow stages.

Configuring Your First Scenario
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The baseline configuration (``config/default.yaml``) provides a starting point. Key parameters to adjust:

* ``countries``: List of ISO 3166-1 alpha-3 country codes to include
* ``aggregation.regions.target_count``: Number of optimization regions (trade-off between detail and solve time)
* ``crops``: Which crops to include in the model
* ``emissions.ghg_price``: Carbon price in USD/tCO2-eq (flows stored in MtCO2-eq internally)
* ``macronutrients``: Minimum dietary requirements

After editing the configuration, create a new named scenario by changing the ``name`` field at the top of the file, then run:

.. code-block:: bash

   tools/smk -j4 --configfile config/<your-name>.yaml

Results will be saved under ``results/<your-name>/``.

Project Structure
-----------------

The repository is organized as follows::

    food-opt/
    ├── config/              # Configuration files for scenarios and parameters
    │   └── config.yaml      # Main configuration file
    ├── data/                # Input data (downloaded and processed)
    │   ├── downloads/       # Raw downloaded datasets
    │   ├── crops.csv        # Crop definitions
    │   ├── foods.csv        # Crop-to-food processing pathways
    │   └── nutrition.csv    # Nutritional content (from USDA FoodData Central)
    ├── processing/          # Intermediate processed datasets
    │   └── {config_name}/   # Processing outputs per scenario
    ├── results/             # Model outputs and analysis
    │   └── {config_name}/   # Results per scenario
    │       ├── build/       # Built model before solving
    │       ├── solved/      # Solved model with optimal values
    │       └── plots/       # Visualizations and CSV exports
    ├── workflow/            # Snakemake workflow
    │   ├── Snakefile        # Main workflow definition
    │   ├── rules/           # Modular rule definitions
    │   └── scripts/         # Data processing and modeling scripts
    ├── tools/               # Utility wrappers
    │   └── smk              # Memory-capped Snakemake wrapper
    ├── notebooks/           # Exploratory analyses
    └── vendor/              # Bundled third-party dependencies

Important Notes
~~~~~~~~~~~~~~~

* The ``results/`` directory contains auto-generated files—never edit these manually
* Always use the ``tools/smk`` wrapper to run Snakemake, as it enforces memory limits to prevent system instability
* The first run will take significant time to download global datasets (~several GB)
