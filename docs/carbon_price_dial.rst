.. SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
..
.. SPDX-License-Identifier: CC-BY-4.0

Interactive: the Carbon Price Dial
==================================

Set a global greenhouse-gas price and watch the GLADE-optimised food system
reorganise. Dominant cropland use and grazing land, net food-system emissions,
the cost-versus-emissions abatement curve, the global diet, and animal feed by
source all respond together as you drag the dial. The widget interpolates
between the model's published carbon-price scenarios.

.. raw:: html

   <style>
   /* This page only: drop the right-hand "On this page" sidebar and let the
      dashboard use the full content width. */
   .toc-drawer { display: none !important; }
   .content { width: auto !important; max-width: none !important; }
   </style>
   <iframe id="carbonDialFrame" title="GLADE Carbon Price Dial"
           src="_static/carbon-dial/index.html" loading="lazy"
           style="width:100%; border:0; height:1500px; overflow:hidden;"></iframe>
   <script>
   window.addEventListener("message", function (e) {
     if (e.data && e.data.carbonDialHeight) {
       var f = document.getElementById("carbonDialFrame");
       if (f) { f.style.height = (e.data.carbonDialHeight + 8) + "px"; }
     }
   });
   </script>

.. note::

   The dial currently shows the **fixed-diet** carbon-price sweep published with
   the GLADE paper: consumption is pinned to the 2020 baseline, while
   production, land use, animal feed and emissions respond to the carbon price.
   A **flexible-diet** mode -- where the diet also re-optimises -- is coming
   soon. Underlying data: the model-output deposition on
   `Zenodo (DOI 10.5281/zenodo.20617942) <https://doi.org/10.5281/zenodo.20617942>`_;
   see :doc:`publications` for how to cite GLADE.
