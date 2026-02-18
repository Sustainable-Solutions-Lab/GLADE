.. SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Development & Contributing
===========================

Overview
--------

This page provides guidance for developers contributing to the ``food-opt`` project, including code conventions and best practices.

For AI coding agents, see ``AGENTS.md`` in the repository root for specific instructions.

Development Setup
-----------------

Prerequisites
~~~~~~~~~~~~~

* Git
* pixi (dependency manager)

Installation
~~~~~~~~~~~~

1. Clone the repository::

       git clone <repository-url>
       cd food-opt

2. Install dependencies::

       pixi install

3. Install development tools::

       pixi install --environment dev

4. Set up prek hooks::

       pixi run --environment dev prek install

   Prek is a lightweight hook runner similar to `pre-commit <https://pre-commit.com/>`_.
   See `prek <https://github.com/j178/prek>`_ for details. You can also use pre-commit.

   Hooks are defined in ``.pre-commit-config.yaml``.

Code Conventions
----------------

Style Guidelines
~~~~~~~~~~~~~~~~

The project uses **ruff** for linting and formatting, enforcing:

* PEP 8 style (with 88-character line length)
* Import sorting (isort)
* Type hints (where practical)
* Docstrings for public functions

**Run linter**::

    pixi run --environment dev ruff check .

**Auto-format code**::

    pixi run --environment dev ruff format .

**Run from prek** (automatic on ``git commit``)::

    pixi run --environment dev prek run --all-files

Specific Conventions
~~~~~~~~~~~~~~~~~~~~

* **Fail early**: Validate external inputs; trust internal invariants
* **Concise logic**: Prefer simple control flow; avoid over-engineering
* **Docstrings**: Use NumPy style for functions with non-obvious behavior

Licensing
~~~~~~~~~

* **Code**: GPL-3.0-or-later (use SPDX header in ``.py`` files)
* **Documentation**: CC-BY-4.0 (use SPDX header in ``.rst``, ``.md`` files)

SPDX headers (required in all source files):

.. code-block:: python

   # SPDX-FileCopyrightText: 2025 <Author>
   #
   # SPDX-License-Identifier: GPL-3.0-or-later

Configuration Validation
-------------------------

The project uses automatic configuration validation to ensure that all configuration files conform to the expected schema. This validation runs at the start of every Snakemake workflow execution.

Schema Definition
~~~~~~~~~~~~~~~~~

The configuration schema is defined in ``config/schemas/config.schema.yaml`` as a JSON Schema (in YAML format). This schema:

* Enforces required fields and their types
* Validates numerical ranges (e.g., percentages must be between 0 and 1)
* Ensures enumerated values are valid (e.g., solver must be "highs" or "gurobi")
* Validates patterns (e.g., country codes must be 3-letter ISO codes)
* Prevents typos through strict property name checking

How It Works
~~~~~~~~~~~~

1. **Automatic validation**: When you run any Snakemake target, the workflow automatically validates the merged configuration (default + user config) against the schema
2. **Clear error messages**: If validation fails, you'll see a detailed error message indicating which field is invalid and why
3. **Snakemake-native**: Uses Snakemake's built-in ``validate()`` function, which internally uses the ``jsonschema`` library

Example validation error::

    ValidationError: 'xyz' is not one of ['highs', 'gurobi']

    Failed validating 'enum' in schema['properties']['solving']['properties']['solver']:
        {'type': 'string', 'enum': ['highs', 'gurobi']}

    On instance['solving']['solver']:
        'xyz'

Common Validation Issues
~~~~~~~~~~~~~~~~~~~~~~~~

**Scientific notation**: YAML parsers may treat scientific notation like ``1e-2`` as strings. Use decimal notation (``0.01``) or explicit floats (``1.0e-2`` with a decimal point) to ensure proper parsing.

**Additional properties**: The schema uses ``additionalProperties: false`` to catch typos. If you add a new configuration field, you must also update the schema.

**Required fields**: All fields present in ``config/default.yaml`` are generally required. User configs only need to specify overrides.

Updating the Schema
~~~~~~~~~~~~~~~~~~~

When adding new configuration options:

1. **Add to** ``config/default.yaml``
2. **Update** ``config/schemas/config.schema.yaml`` with:

   * Property definition under the appropriate section
   * Type constraints (``type: string``, ``type: number``, etc.)
   * Validation rules (``minimum``, ``maximum``, ``pattern``, ``enum``, etc.)
   * Description for documentation

3. **Test** by running the workflow::

       tools/smk --configfile config/validation.yaml -n

4. **Verify** that both valid and invalid configurations are handled correctly

For more information on JSON Schema syntax, see https://json-schema.org/understanding-json-schema/.

Testing
-------

The project uses **pytest** for integration testing via the Snakemake Python API. Tests live in ``tests/`` and exercise the full workflow pipeline using a lightweight configuration with reduced spatial resolution.

Test Configuration
~~~~~~~~~~~~~~~~~~

Two dedicated config files drive the test suite:

* **``tests/config/test.yaml``**: Minimal overrides on top of ``default.yaml`` — 200 regions, 2 resource classes, 9 crops, 14 trade hubs. Outputs to ``results/test/``.
* **``tests/config/test_scenarios.yaml``**: Two scenarios (``default`` and ``G``) to exercise the scenario mechanism and GHG pricing code path.

Running Tests
~~~~~~~~~~~~~

Tests require the ``dev`` environment::

    pixi run -e dev test              # all tests
    pixi run -e dev test-integration  # dryrun + build/solve/analysis
    pixi run -e dev test-no-plots     # skip figure generation tests
    pixi run -e dev pytest -v         # verbose output

Test Markers
~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Marker
     - Description
   * - ``integration``
     - Full Snakemake workflow tests (DAG dryrun + build/solve/analysis)
   * - ``plots``
     - Figure generation tests (optional, slower)

How It Works
~~~~~~~~~~~~

Tests call a shared helper ``run_snakemake_target()`` in ``tests/conftest.py`` that invokes the Snakemake Python API directly (no subprocess). The helper layers ``tests/config/test.yaml`` on top of ``config/default.yaml`` and targets specific output files.

* **Dryrun test** (``test_workflow_dryrun``): Validates full DAG construction with ``forceall=True`` without executing anything. Does not require credentials or downloaded data. Catches missing inputs, broken rules, and invalid wildcard patterns.
* **Execution test** (``test_build_solve_analyze``): Runs the actual pipeline through analysis for the default scenario. Requires USDA/ECMWF credentials for data downloads on first run.
* **Plot test** (``test_plots``): Generates representative plots from solved model outputs.

Tests never delete ``results/test/`` or ``.snakemake/``; Snakemake detects up-to-date outputs and skips them automatically, so subsequent runs are near-instant when code hasn't changed.

Writing New Tests
~~~~~~~~~~~~~~~~~

Add new test files as ``tests/test_*.py``. Use the ``run_snakemake_target()`` helper for workflow-level tests and standard pytest patterns for unit tests::

    # tests/test_my_feature.py
    import pytest
    from conftest import run_snakemake_target, RESULTS_DIR

    @pytest.mark.integration
    def test_my_new_output():
        run_snakemake_target("results/test/analysis/scen-default/my_output.csv")
        assert (RESULTS_DIR / "analysis" / "scen-default" / "my_output.csv").exists()

Repository Structure
--------------------

::

    food-opt/
    ├── config/              # Scenario configuration files
    ├── data/                # Input data (small tracked files; large downloads ignored)
    ├── docs/                # Documentation (Sphinx)
    ├── processing/          # Intermediate outputs (not committed)
    ├── results/             # Model results (not committed)
    ├── tests/               # pytest integration tests
    ├── workflow/            # Snakemake workflow
    │   ├── Snakefile        # Main workflow definition
    │   ├── rules/           # Modular rule files
    │   └── scripts/         # Python scripts for processing/modeling
    ├── tools/               # Utility wrappers (e.g., smk)
    ├── notebooks/           # Exploratory Jupyter notebooks
    ├── vendor/              # Bundled third-party code (customized PyPSA/linopy)
    ├── .gitignore
    ├── pixi.toml            # Dependencies and environments
    ├── ruff.toml            # Linter configuration
    ├── README.md
    └── AGENTS.md            # AI agent guidance

Adding New Features
-------------------

Adding a New Visualization
~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Create script** ``workflow/scripts/plotting/plot_my_metric.py``:

   .. code-block:: python

      import pypsa
      import matplotlib.pyplot as plt

      n = pypsa.Network(snakemake.input.network)

      # Extract and process data
      metric_data = extract_my_metric(n)

      # Plot
      fig, ax = plt.subplots()
      metric_data.plot(kind="bar", ax=ax)
      ax.set_ylabel("My Metric")
      ax.set_title("My Analysis")

      plt.savefig(snakemake.output.plot, bbox_inches="tight")

2. **Add rule** in ``workflow/rules/plotting.smk``:

   .. code-block:: python

      rule plot_my_metric:
          input:
              network="results/{name}/solved/model_scen-{scenario}.nc"
          output:
              plot="results/{name}/plots/scen-{scenario}/my_metric.pdf"
          script:
              "../scripts/plotting/plot_my_metric.py"

3. **Add to** ``all`` **rule** (optional):

   .. code-block:: python

      rule all:
          input:
              # ...
              f"results/{name}/plots/scen-{scenario}/my_metric.pdf"

4. **Run**::

       tools/smk -j4 --configfile config/my_scenario.yaml -- results/my_scenario/plots/scen-default/my_metric.pdf

Version Control
---------------

Git Workflow
~~~~~~~~~~~~

1. **Branch for features**::

       git checkout -b feature/my-new-feature

2. **Commit frequently** with descriptive messages::

       git commit -m "feat: Add minimum legume production constraint"

3. **Push to remote**::

       git push origin feature/my-new-feature

4. **Create pull request** for review

Commit Messages
~~~~~~~~~~~~~~~

Follow conventional commit style:

* ``feat: Add new crop to GAEZ mapping``
* ``fix: Correct water requirement unit conversion``
* ``docs: Update health module documentation``
* ``refactor: Simplify resource class computation``
* ``test: Add validation for quickstart config``

What to Commit
~~~~~~~~~~~~~~

**DO commit**:

* Code (``.py``, ``.smk``)
* Configuration (``.yaml``)
* Documentation (``.rst``, ``.md``)
* Static data files (``data/*.csv`` if < 1 MB)

**DO NOT commit**:

* Downloaded datasets (``data/downloads/``)
* Processed intermediate files (``processing/``)
* Results (``results/``)
* Large binary files (> 1 MB)

These are excluded via ``.gitignore``.

Documentation
-------------

Building Documentation Locally
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    tools/build-docs -j4
    # Open docs/_build/html/index.html in browser


Updating Documentation
~~~~~~~~~~~~~~~~~~~~~~

1. **Edit** ``.rst`` files in ``docs/``
2. **Rebuild**::

       tools/build-docs -j4

3. **Check** for warnings/errors
4. **Commit** documentation changes

Docstring Guidelines
~~~~~~~~~~~~~~~~~~~~

Use NumPy-style docstrings:

.. code-block:: python

   def my_function(param1: int, param2: str) -> float:
       """One-line summary.

       Longer description if needed, explaining purpose, algorithm, etc.

       Parameters
       ----------
       param1 : int
           Description of param1
       param2 : str
           Description of param2

       Returns
       -------
       float
           Description of return value

       Raises
       ------
       ValueError
           If param1 is negative

       Notes
       -----
       Additional implementation notes, references, etc.
       """

Contributing Guidelines
-----------------------

Before Submitting a Pull Request
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Run linter**: ``pixi run --environment dev ruff check . && pixi run --environment dev ruff format .`` (this is taken care of automatically if you set up ``prek``)
2. **Run tests**: ``pixi run -e dev test-integration`` (at minimum the dryrun test should pass)
3. **Test workflow**: Verify that the default configuration runs successfully
4. **Update documentation**: If changing user-facing behavior
5. **Write commit messages**: Descriptive and following conventions

Pull Request Process
~~~~~~~~~~~~~~~~~~~~~

1. Fork the repository
2. Create a feature branch
3. Make changes with clear commits
4. Push to your fork
5. Open pull request with description of changes
6. Address review feedback
7. Merge once approved
