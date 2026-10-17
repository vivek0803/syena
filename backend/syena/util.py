"""Small shared helpers."""
import math
from datetime import date, datetime

import numpy as np


def jsonable(x):
    """Convert numpy/pandas scalars and datetimes recursively into JSON-safe Python values."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    return x
