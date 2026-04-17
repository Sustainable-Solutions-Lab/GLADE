.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Cluster Execution
=================

When running large scenario sweeps (e.g., global sensitivity analysis with
thousands of scenarios), solving locally becomes impractical.  The cluster
execution workflow offloads solve+analyze jobs to an HPC cluster using SLURM,
**without requiring Snakemake on the cluster**.

Instead, a JSON *manifest* is generated locally that captures all resolved
inputs, parameters, and outputs for each scenario.  On the cluster, a
lightweight runner executes each scenario directly from the manifest, avoiding
Snakemake's DAG construction overhead and reducing sensitivity to HPC
filesystem latency.

Overview
--------

The workflow has four phases:

1. **Build & calibrate** (local): Run Snakemake to build the model and solve
   any prerequisite scenarios (e.g., baselines that generate consumer values
   for downstream scenarios).

2. **Export manifest** (local): Generate a JSON manifest describing all
   remaining scenarios to solve on the cluster.

3. **Sync & submit** (local → cluster): Transfer inputs, manifest, and tools
   to the cluster, then submit a SLURM job array.

4. **Collect results** (cluster → local): Sync analysis outputs back for
   post-processing.

Prerequisites
-------------

On the cluster:

- ``pixi`` installed and ``pixi install`` (or ``pixi install -e gurobi``)
  run in the project directory.
- Passwordless SSH access from your local machine (key-based auth or an
  active ``ControlMaster`` session).

The cluster does **not** need a full Snakemake installation.  Only the
``pixi`` environment with the solver and Python dependencies is required.

Tools
-----

Four tools implement the cluster workflow:

``tools/export-solve-manifest``
    Generates a JSON manifest file containing fully-resolved inputs,
    parameters, and output paths for each scenario.  Uses the same
    configuration and scenario machinery as the Snakemake rules but runs
    independently (no DAG construction).

    Key options:

    - ``--exclude "pattern" ...``: Exclude scenarios by name (supports
      shell-style wildcards).  Useful for skipping baselines already solved
      locally.
    - ``--skip-existing``: Omit scenarios whose output files already exist.
    - ``--scenarios S1 S2 ...``: Only include specific scenarios.
    - ``-o PATH``: Custom output path (default: ``.batch/manifest_{name}.json``).

``tools/sync-solve-inputs``
    Syncs all files needed on the cluster: built model, processing outputs,
    static data, consumer values, the manifest, workflow scripts, config
    files, and the cluster tools.  Supports ``rsync`` (default) or
    ``tar+ssh`` (``--tar``, faster for many small files).

``tools/batch-solve``
    Reads the manifest and submits a SLURM job array.  Each array element
    runs a batch of scenarios in parallel using ``tools/cluster-solve``.

    Key options:

    - ``--manifest PATH``: Path to the manifest JSON (required).
    - ``-j N``: Concurrent solves per array element (default: 4).
    - ``--batch-size N``: Scenarios per array element (default: 50).
    - ``-e ENV``: Pixi environment (e.g., ``gurobi``).
    - ``--partition``, ``--account``: SLURM partition and account.
    - ``--time``, ``--mem``: Override computed SLURM resource defaults.
    - ``--dry-run``: Print the generated sbatch script without submitting.

``tools/cluster-solve``
    Runs solve+analyze for a single scenario from a manifest entry.
    Constructs a lightweight namespace shim and calls ``run_solve`` /
    ``run_analysis`` directly — no Snakemake imports.

    Can be invoked by scenario name or by index into the manifest::

        pixi run python tools/cluster-solve manifest.json <scenario-name>
        pixi run python tools/cluster-solve manifest.json --index <N>

Step-by-Step Example
--------------------

The example below uses a global sensitivity analysis config (``gsa.yaml``)
with ~24,000 scenarios.

**1. Build and solve baselines locally**

Baseline scenarios generate consumer values (dual variables) that downstream
GSA scenarios depend on.  Solve them with Snakemake::

    # Locally: build model + solve baselines + first GSA scenarios
    SMK_MEM_MAX=40G tools/smk -e gurobi -j5 \
        --configfile config/gsa.yaml \
        -- results/gsa/analysis/scen-gsa{,-l1-low,-l1-high}_0/objective_breakdown.parquet

**2. Export the manifest**

Generate the manifest, excluding baselines (already solved) and skipping
scenarios with existing outputs::

    pixi run python tools/export-solve-manifest config/gsa.yaml \
        --exclude "baseline*" "default" \
        --skip-existing

This writes ``.batch/manifest_gsa.json`` in ~15 seconds.

**3. Sync to the cluster**

Transfer inputs, manifest, and scripts::

    tools/sync-solve-inputs gsa.yaml <ssh-host> </path/to/remote/food-opt>

If the remote path is given with ``~`` (e.g. ``~/food-opt``), single-quote it so
the local shell does not expand it to the local home directory::

    tools/sync-solve-inputs gsa.yaml <ssh-host> '~/food-opt'

**4. Submit on the cluster**

SSH to the cluster and submit::

    cd </path/to/remote/food-opt>
    pixi run python tools/batch-solve \
        --manifest .batch/manifest_gsa.json \
        -j6 -e gurobi \
        --partition <partition> --account <account>

Monitor progress::

    squeue -u $USER -n solve_gsa

**5. Collect results**

Sync analysis outputs back to your local machine::

    rsync -a --info=progress2 \
        "<ssh-host>:</path/to/remote/food-opt>/results/gsa/analysis/" \
        results/gsa/analysis

**6. Post-processing**

Run downstream analysis (e.g., PCE sensitivity) locally using Snakemake::

    tools/smk -j20 --configfile config/gsa.yaml \
        --allowed-rules compute_sobol_sensitivity <other_rules> \
        -- sobol_plots

Manifest Format
---------------

The manifest is a JSON file with this structure:

.. code-block:: json

    {
      "config_name": "gsa",
      "config_file": "/path/to/config/gsa.yaml",
      "inline_analysis": true,
      "solving": {
        "mem_mb": 9000,
        "runtime": 30,
        "time_limit": 60,
        "threads": 6
      },
      "shared_params": {
        "countries": ["AFG", "AGO", "..."],
        "slack_marginal_cost": 100.0,
        "..."
      },
      "scenarios": [
        {
          "scenario": "gsa_0",
          "inputs": {"network": "results/gsa/build/model.nc", "..."},
          "params": {"ghg_price": 8.48, "sensitivity": {"..."}},
          "outputs": {"objective_breakdown": "results/gsa/analysis/scen-gsa_0/objective_breakdown.parquet", "..."},
          "log": "logs/gsa/solve_and_analyze_model_scen-gsa_0.log"
        }
      ]
    }

``shared_params`` contains structural parameters that are identical across
all scenarios (e.g., country list, residue constraints).  These are factored
out to reduce manifest size.  ``tools/cluster-solve`` merges them back into
each scenario's params at runtime.

Re-running Failed Scenarios
---------------------------

If some scenarios fail (solver timeout, infeasibility), you can re-export the
manifest with ``--skip-existing`` to get only the missing ones, re-sync, and
re-submit::

    # Locally
    pixi run python tools/export-solve-manifest config/gsa.yaml \
        --exclude "baseline*" "default" --skip-existing

    tools/sync-solve-inputs gsa.yaml <ssh-host> </path/to/remote/food-opt>

    # On cluster
    pixi run python tools/batch-solve --manifest .batch/manifest_gsa.json \
        -j6 -e gurobi --partition <partition> --account <account>

Each ``cluster-solve`` invocation also checks ``--skip-existing`` at runtime,
so re-submitting is always safe — already-completed scenarios are skipped.

Keeping the Manifest in Sync
-----------------------------

The manifest generator (``tools/export-solve-manifest``) mirrors the input
and parameter definitions from the ``solve_model`` / ``solve_and_analyze_model``
Snakemake rules.  It is intentionally decoupled from Snakemake for performance
(~15s vs ~5min via the Snakemake API for 24k scenarios).

When adding or changing inputs or parameters on these rules, **also update
the manifest generator**.  Comments on the rules and on ``solve_model_inputs``
in ``workflow/rules/model.smk`` serve as reminders.
