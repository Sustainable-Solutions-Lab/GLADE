.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Introduction
============

What ``food-opt`` is
--------------------

``food-opt`` is a global food systems optimization model for exploring
trade-offs between nutritional and environmental outcomes. It can be used to
answer questions like: *How could we feed the world's population while
minimizing greenhouse gas emissions and diet-related disease burden? What are
the trade-offs and synergies between environmental sustainability and food
security?*

The model represents the global food system as a network of material flows —
from land and water inputs, through crop production, livestock systems and
trade, to processed foods and human consumption. It then uses **linear
programming** to find the combination of production, conversion, trade, and
consumption choices that best achieves a specified objective while respecting
physical constraints on land, water, yields, and nutritional adequacy.

Modeling approach
-----------------

``food-opt`` is built on `PyPSA <https://pypsa.org/>`_ (Python for Power
System Analysis), an open-source framework originally designed for energy
system modeling. We adapt PyPSA's flexible, component-based network
representation to describe food flows rather than energy flows: buses
represent commodities (crops, foods, feeds, nutrients, emissions), links
represent conversion and transport, and stores and generators represent
resources and sinks. PyPSA automatically translates this component graph into
a linear program.

The workflow is orchestrated by `Snakemake
<https://snakemake.readthedocs.io/>`_, which tracks dependencies between
preprocessing, model building, solving, and analysis steps and only re-runs
what has changed. This keeps results reproducible across scenarios and makes
it easy to rerun narrow parts of the pipeline when you change a single input.

For the mathematical formulation, see :doc:`model_framework`. For component
naming conventions and the supply-chain topology, see :doc:`land_use`,
:doc:`crop_production`, :doc:`livestock`, and :doc:`food_processing`.

Scope at a glance
-----------------

The model covers:

* **Crops**: more than 60 crops with spatially explicit yield potentials from
  `GAEZ <https://gaez.fao.org/>`_, including multi-cropping pathways.
* **Livestock**: grazing- and feed-based systems for meat, milk, and eggs,
  with enteric and manure emissions.
* **Trade and processing**: hub-based international trade for crops, foods,
  and feeds, with processing pathways that produce co-products and by-products.
* **Nutrition**: per-country food-group and macronutrient constraints,
  optionally linked to dietary risk factors from the Global Burden of Disease
  study.
* **Environment**: greenhouse gas emissions (CO₂, CH₄, N₂O), land-use change
  carbon fluxes, fertilizer nitrogen balances, and basin-level water limits.

Spatial resolution is configurable: the world is divided into sub-national
optimization regions (typically 100–400), each with its own land endowment,
crop yields, water budget, and dietary requirements. Input geophysical data
is used at 0.05° × 0.05° resolution before aggregation.

Prerequisites
-------------

System requirements
~~~~~~~~~~~~~~~~~~~

* **Operating system**: Linux is the primary supported platform; macOS works
  as well. On Windows, use WSL2.
* **Disk space**: plan for ~30 GB total (raw downloads, processed data,
  environment, results for a few scenarios).
* **Memory**: 8 GB is enough for low-resolution scenarios (e.g. the tutorial
  configurations with 100 regions); full-resolution solves at 400 regions
  typically need 16–32 GB.
* **Solver**: the open-source `HiGHS <https://highs.dev/>`_ solver is
  installed automatically and suffices for most cases.
  `Gurobi <https://www.gurobi.com/>`_ is supported via the ``gurobi`` and
  ``dev-gurobi`` pixi environments and is substantially faster for large
  problems, but requires a licence (free academic licences are available).

Software to install manually
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* `Git <https://git-scm.com/>`_ — to clone the repository.
* `pixi <https://pixi.sh/>`_ — cross-platform package manager that handles
  every other dependency, including Python, Snakemake, PyPSA, geopandas, and
  the HiGHS solver.

Accounts and credentials
~~~~~~~~~~~~~~~~~~~~~~~~

Three health/dietary datasets cannot be redistributed and must be downloaded
manually after free registration:

* **IHME GBD 2023 mortality rates** — `IHME GBD Results Tool
  <https://vizhub.healthdata.org/gbd-results/>`_.
* **IHME GBD 2019 relative risk data** — same source, separate export.
* **Global Dietary Database** — `GDD <https://www.globaldietarydatabase.org/>`_.

Two API credentials are needed for automatic downloads:

* **Copernicus Climate Data Store** — required for satellite land-cover data.
  Register at https://cds.climate.copernicus.eu/user/register, accept the
  land-cover dataset licence, and copy the API key from your profile.
* **USDA FoodData Central** — optional; the repository ships pre-fetched
  nutritional data. A free key from https://fdc.nal.usda.gov/api-key-signup
  is only needed if you want to refresh that data.

Installation
------------

1. **Clone the repository**:

   .. code-block:: bash

      git clone https://github.com/Sustainable-Solutions-Lab/food-opt.git
      cd food-opt

2. **Install dependencies**:

   .. code-block:: bash

      pixi install

   This downloads Python, Snakemake, the HiGHS solver, and the rest of the
   stack into a project-local environment. It takes a few minutes the first
   time. For the Gurobi solver, use ``pixi install --environment gurobi``
   instead.

   .. note::

      **Older Linux systems (e.g. compute clusters)**: pixi assumes a
      minimum glibc version of 2.28 by default. If ``ldd --version``
      reports an older glibc, add the following to ``pixi.toml`` and rerun
      ``pixi update``:

      .. code-block:: toml

         [system-requirements]
         libc = { family = "glibc", version = "2.17" }

      Replace ``"2.17"`` with the version reported by ``ldd --version``.

3. **Set up API credentials**:

   .. code-block:: bash

      cp config/secrets.yaml.example config/secrets.yaml

   Edit ``config/secrets.yaml`` and fill in your ECMWF Climate Data Store
   credentials (and optionally the USDA key). Alternatively, set the
   equivalent environment variables:

   .. code-block:: bash

      export ECMWF_DATASTORES_URL="https://cds.climate.copernicus.eu/api"
      export ECMWF_DATASTORES_KEY="your-uid:your-api-key"
      export USDA_API_KEY="your-usda-api-key"

4. **Download the manually-licensed datasets**: follow the
   :ref:`manual-download-checklist` in :doc:`data_sources` to place the three
   IHME/GDD files under ``data/manually_downloaded/``.

5. **Verify the setup** with a dry run:

   .. code-block:: bash

      tools/smk -j4 --configfile config/tutorial/01_ghg_prices.yaml -n

   The ``-n`` flag asks Snakemake to show what *would* run without executing
   anything. If this completes without errors, your environment is ready for
   the :doc:`tutorial`.

Repository layout
-----------------

The repository is organised as follows::

    food-opt/
    ├── config/              # Scenario configuration files (YAML)
    │   ├── default.yaml     # Default values for every configurable key
    │   ├── example.yaml     # Minimal override template
    │   └── tutorial/        # Configs used by the tutorial
    ├── data/                # Input data (downloaded and curated)
    ├── processing/          # Intermediate outputs, per scenario
    ├── results/             # Final outputs, per scenario
    │   └── {name}/
    │       ├── build/       # Built PyPSA networks (pre-solve)
    │       ├── solved/      # Solved networks
    │       ├── analysis/    # Extracted parquet statistics
    │       └── plots/       # Auto-generated figures
    ├── workflow/            # Snakemake rules and scripts
    │   ├── Snakefile
    │   ├── rules/
    │   └── scripts/
    ├── tools/               # Wrappers (e.g. memory-capped `smk`)
    ├── notebooks/           # Exploratory analyses
    └── docs/                # This documentation (Sphinx)

A few conventions worth knowing up front:

* Never edit files under ``results/`` or ``processing/`` by hand — they are
  regenerated from config. Rerun the relevant Snakemake target instead.
* Always invoke Snakemake via ``tools/smk`` rather than ``snakemake``
  directly; the wrapper enforces memory limits that prevent the system from
  swapping itself to death.
* All configuration fields in ``config/default.yaml`` can be overridden in
  your own config file, which typically contains only a ``name`` and the keys
  you want to change.

Where to go next
----------------

* :doc:`tutorial` — a hands-on walkthrough that builds two small scenario sets
  from scratch and analyses the results in a notebook. Start here if you have
  just finished installing.
* :doc:`configuration` — full reference for configuration keys, scenario
  overrides, and the programmatic scenario-generator DSL.
* :doc:`workflow` — description of the Snakemake pipeline, its stages, and
  how rules depend on each other.
* :doc:`results` and :doc:`analysis` — what the solver produces and how to
  extract and interpret standardised statistics.
* :doc:`model_framework` — the mathematical formulation of the LP.
