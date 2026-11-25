"""IRRBB: Interest Rate Risk in the Banking Book measurement engine.

The package computes, for a multi-currency banking book:

* MtM / present value of every position under base and shocked curves;
* ΔEVE by full revaluation and by the BCBS 368 standardised approach;
* NII and ΔNII over a configurable horizon under a constant balance sheet;
* repricing gap, position PV01 and key-rate DV01;
* supervisory outlier tests.

Typical usage::

    from irrbb.config import RunConfig
    from irrbb.engine import run_from_frames

    result = run_from_frames(RunConfig(as_of=as_of), curve_quotes, fx, positions)
    result["eve_regulatory"]
"""

__version__ = "0.4.1"
