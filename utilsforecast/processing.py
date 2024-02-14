# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/processing.ipynb.

# %% auto 0
__all__ = ['to_numpy', 'counts_by_id', 'maybe_compute_sort_indices', 'assign_columns', 'drop_columns', 'take_rows',
           'filter_with_mask', 'is_nan', 'is_none', 'is_nan_or_none', 'match_if_categorical', 'vertical_concat',
           'horizontal_concat', 'copy_if_pandas', 'join', 'drop_index_if_pandas', 'rename', 'sort', 'offset_times',
           'offset_dates', 'time_ranges', 'repeat', 'cv_times', 'group_by', 'group_by_agg', 'is_in', 'between',
           'fill_null', 'cast', 'value_cols_to_numpy', 'make_future_dataframe', 'anti_join', 'process_df',
           'DataFrameProcessor', 'backtest_splits', 'add_insample_levels']

# %% ../nbs/processing.ipynb 2
import re
import reprlib
import warnings
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from pandas.tseries.offsets import BaseOffset

from .compat import DataFrame, Series, pl, pl_DataFrame, pl_Series
from utilsforecast.validation import (
    _is_dt_dtype,
    _is_int_dtype,
    ensure_shallow_copy,
    validate_format,
)

# %% ../nbs/processing.ipynb 5
def _polars_categorical_to_numerical(serie: pl_Series) -> pl_Series:
    if serie.dtype == pl.Categorical:
        serie = serie.to_physical()
    return serie

# %% ../nbs/processing.ipynb 6
def to_numpy(df: DataFrame) -> np.ndarray:
    if isinstance(df, pd.DataFrame):
        cat_cols = [
            c
            for c, dtype in df.dtypes.items()
            if isinstance(dtype, pd.CategoricalDtype)
        ]
        if cat_cols:
            df = df.copy(deep=False)
            df = ensure_shallow_copy(df)
            for col in cat_cols:
                df[col] = df[col].cat.codes
        df = df.to_numpy()
    else:
        try:
            expr = pl.all().map_batches(_polars_categorical_to_numerical)
        except AttributeError:
            expr = pl.all().map(_polars_categorical_to_numerical)
        df = df.select(expr).to_numpy(order="c")
    return df

# %% ../nbs/processing.ipynb 7
def counts_by_id(df: DataFrame, id_col: str) -> DataFrame:
    if isinstance(df, pd.DataFrame):
        id_counts = df.groupby(id_col, observed=True).size()
        if not id_counts.index.is_monotonic_increasing:
            id_counts = id_counts.sort_index()
        id_counts = id_counts.reset_index()
    else:
        id_counts = df[id_col].value_counts().sort(id_col)
    id_counts.columns = [id_col, "counts"]
    return id_counts

# %% ../nbs/processing.ipynb 8
def maybe_compute_sort_indices(
    df: DataFrame, id_col: str, time_col: str
) -> Optional[np.ndarray]:
    """Compute indices that would sort dataframe

    Parameters
    ----------
    df : pandas or polars DataFrame
        Input dataframe with id, times and target values.

    Returns
    -------
    numpy array or None
        Array with indices to sort the dataframe or None if it's already sorted.
    """
    if isinstance(df, pd.DataFrame):
        idx = pd.MultiIndex.from_frame(df[[id_col, time_col]])
    else:
        # this was faster than trying to build the multi index from polars
        sort_idxs = df.select(pl.arg_sort_by([id_col, time_col]).alias("idx"))["idx"]
        idx = pd.Index(sort_idxs.to_numpy())
    if idx.is_monotonic_increasing:
        return None
    if isinstance(df, pd.DataFrame):
        sort_idxs = idx.argsort()
    return sort_idxs

# %% ../nbs/processing.ipynb 9
def assign_columns(
    df: DataFrame,
    names: Union[str, List[str]],
    values: Union[np.ndarray, pd.Series, pl_Series],
) -> DataFrame:
    if isinstance(df, pd.DataFrame):
        df[names] = values
    else:
        is_scalar = isinstance(values, str) or not hasattr(values, "__len__")
        if is_scalar:
            assert isinstance(names, str)
            vals: Union[pl_DataFrame, pl_Series, pl.Expr] = pl.lit(values).alias(names)
        elif isinstance(values, pl_Series):
            assert isinstance(names, str)
            vals = values.alias(names)
        else:
            if isinstance(names, str):
                names = [names]
            vals = pl.from_numpy(values, schema=names, orient="row")
        df = df.with_columns(vals)
    return df

# %% ../nbs/processing.ipynb 12
def drop_columns(df: DataFrame, columns: Union[str, List[str]]) -> DataFrame:
    if isinstance(df, pd.DataFrame):
        df = df.drop(columns=columns)
    else:
        df = df.drop(columns)
    return df

# %% ../nbs/processing.ipynb 13
def take_rows(df: Union[DataFrame, Series, np.ndarray], idxs: np.ndarray) -> DataFrame:
    if isinstance(df, (pd.DataFrame, pd.Series)):
        df = df.iloc[idxs]
    else:
        df = df[idxs]
    return df

# %% ../nbs/processing.ipynb 15
def filter_with_mask(
    df: Union[Series, DataFrame, pd.Index, np.ndarray],
    mask: Union[np.ndarray, pd.Series, pl_Series],
) -> DataFrame:
    if isinstance(df, (pd.DataFrame, pd.Series, pd.Index, np.ndarray)):
        out = df[mask]
    else:
        out = df.filter(mask)  # type: ignore
    return out

# %% ../nbs/processing.ipynb 16
def is_nan(s: Series) -> Series:
    if isinstance(s, pd.Series):
        out = s.isna()
    else:
        out = s.is_nan()
    return out

# %% ../nbs/processing.ipynb 18
def is_none(s: Series) -> Series:
    if isinstance(s, pd.Series):
        out = is_nan(s)
    else:
        out = s.is_null()
    return out

# %% ../nbs/processing.ipynb 20
def is_nan_or_none(s: Series) -> Series:
    return is_nan(s) | is_none(s)

# %% ../nbs/processing.ipynb 22
def match_if_categorical(
    s1: Union[Series, pd.Index], s2: Series
) -> Tuple[Series, Series]:
    if isinstance(s1.dtype, pd.CategoricalDtype):
        if isinstance(s1, pd.Index):
            cat1 = s1.categories
        else:
            cat1 = s1.cat.categories
        if isinstance(s2.dtype, pd.CategoricalDtype):
            cat2 = s2.cat.categories
        else:
            cat2 = s2.unique().astype(cat1.dtype)
        missing = set(cat2) - set(cat1)
        if missing:
            # we assume the original is s1, so we extend its categories
            new_dtype = pd.CategoricalDtype(categories=cat1.tolist() + sorted(missing))
            s1 = s1.astype(new_dtype)
            s2 = s2.astype(new_dtype)
    elif isinstance(s1, pl_Series) and s1.dtype == pl.Categorical:
        with pl.StringCache():
            cat1 = s1.cat.get_categories()
            if s2.dtype == pl.Categorical:
                cat2 = s2.cat.get_categories()
            else:
                cat2 = s2.unique().sort().cast(cat1.dtype)
            # populate cache, keep original categories first
            pl.concat([cat1, cat2]).cast(pl.Categorical)
            s1 = s1.cast(pl.Utf8).cast(pl.Categorical)
            s2 = s2.cast(pl.Utf8).cast(pl.Categorical)
    return s1, s2

# %% ../nbs/processing.ipynb 23
def vertical_concat(
    dfs: List[Union[DataFrame, Series]], match_categories: bool = True
) -> Union[DataFrame, Series]:
    if not dfs:
        raise ValueError("Can't concatenate empty list.")
    if isinstance(dfs[0], pd.Series):
        out = pd.concat(dfs).reset_index(drop=True)
    elif isinstance(dfs[0], pl_Series):
        out = pl.concat(dfs)
    elif isinstance(dfs[0], pd.DataFrame):
        cat_cols = [
            c
            for c, dtype in dfs[0].dtypes.items()
            if isinstance(dtype, pd.CategoricalDtype)
        ]
        if match_categories and cat_cols:
            if len(dfs) > 2:
                raise NotImplementedError(
                    "Categorical replacement for more than two dataframes"
                )
            assert len(dfs) == 2
            df1, df2 = dfs
            df1 = df1.copy(deep=False)
            df2 = df2.copy(deep=False)
            for col in cat_cols:
                s1, s2 = match_if_categorical(df1[col], df2[col])
                df1[col] = s1
                df2[col] = s2
            dfs = [df1, df2]
        out = pd.concat(dfs).reset_index(drop=True)
    else:
        all_cols = dfs[0].columns
        cat_cols = [
            all_cols[i]
            for i, dtype in enumerate(dfs[0].dtypes)
            if dtype == pl.Categorical
        ]
        if match_categories and cat_cols:
            if len(dfs) > 2:
                raise NotImplementedError(
                    "Categorical replacement for more than two dataframes"
                )
            assert len(dfs) == 2
            df1, df2 = dfs
            for col in cat_cols:
                s1, s2 = match_if_categorical(df1[col], df2[col])
                df1 = df1.with_columns(s1)
                df2 = df2.with_columns(s2)
            dfs = [df1, df2]
        out = pl.concat(dfs)
    return out

# %% ../nbs/processing.ipynb 27
def horizontal_concat(dfs: List[DataFrame]) -> DataFrame:
    if not dfs:
        raise ValueError("Can't concatenate empty list.")
    if isinstance(dfs[0], pd.DataFrame):
        out = pd.concat(dfs, axis=1)
    elif isinstance(dfs[0], pl_DataFrame):
        out = pl.concat(dfs, how="horizontal")
    else:
        raise ValueError(f"Got list of unexpected types: {type(dfs[0])}.")
    return out

# %% ../nbs/processing.ipynb 29
def copy_if_pandas(df: DataFrame, deep: bool = False) -> DataFrame:
    if isinstance(df, pd.DataFrame):
        df = df.copy(deep=deep)
    return df

# %% ../nbs/processing.ipynb 30
def join(
    df1: Union[DataFrame, Series],
    df2: Union[DataFrame, Series],
    on: Union[str, List[str]],
    how: str = "inner",
) -> DataFrame:
    if isinstance(df1, (pd.Series, pl_Series)):
        df1 = df1.to_frame()
    if isinstance(df2, (pd.Series, pl_Series)):
        df2 = df2.to_frame()
    if isinstance(df1, pd.DataFrame):
        out = df1.merge(df2, on=on, how=how)
    else:
        out = df1.join(df2, on=on, how=how)  # type: ignore
    return out

# %% ../nbs/processing.ipynb 31
def drop_index_if_pandas(df: DataFrame) -> DataFrame:
    if isinstance(df, pd.DataFrame):
        df = df.reset_index(drop=True)
    return df

# %% ../nbs/processing.ipynb 32
def rename(df: DataFrame, mapping: Dict[str, str]) -> DataFrame:
    if isinstance(df, pd.DataFrame):
        df = df.rename(columns=mapping, copy=False)
    else:
        df = df.rename(mapping)
    return df

# %% ../nbs/processing.ipynb 33
def sort(df: DataFrame, by: Optional[Union[str, List[str]]] = None) -> DataFrame:
    if isinstance(df, pd.DataFrame):
        out = df.sort_values(by).reset_index(drop=True)
    elif isinstance(df, (pd.Series, pd.Index)):
        out = df.sort_values()
        if isinstance(out, pd.Series):
            out = out.reset_index(drop=True)
    elif isinstance(df, pl_DataFrame):
        out = df.sort(by)
    else:
        out = df.sort()
    return out

# %% ../nbs/processing.ipynb 36
def _multiply_pl_freq(freq: str, n: Union[int, Series]) -> str:
    freq_n, freq_offset = re.findall(r"(\d+)(\w+)", freq)[0]
    freq_n = int(freq_n)
    if isinstance(n, int):
        total_n = freq_n * n
        out = f"{total_n}{freq_offset}"
    else:
        try:
            is_int = n.dtype.is_integer()
        except AttributeError:
            is_int = n.is_integer()
        if not is_int:
            raise ValueError("`n` must be an integer or a polars series of integers.")
        out = (n * freq_n).cast(pl.Utf8) + freq_offset
    return out

# %% ../nbs/processing.ipynb 38
def _ensure_month_ends(
    times: pl_Series, orig_times: pl_Series, freq: Union[str, int, BaseOffset]
) -> pl_Series:
    if not isinstance(freq, str) or "mo" not in freq:
        return times
    next_days = orig_times.dt.offset_by("1d")
    month_ends = (next_days.dt.month() != orig_times.dt.month()).all()
    if month_ends:
        times = times.dt.month_end()
    return times

# %% ../nbs/processing.ipynb 39
def offset_times(
    times: Union[Series, pd.Index],
    freq: Union[int, str, BaseOffset],
    n: Union[int, np.ndarray],
) -> Union[Series, pd.Index]:
    if isinstance(times, (pd.Series, pd.Index)):
        if isinstance(freq, str):
            freq = pd.tseries.frequencies.to_offset(freq)
        ints = _is_int_dtype(times) and isinstance(freq, int)
        dts = _is_dt_dtype(times) and isinstance(freq, BaseOffset)
        if not ints and not dts:
            raise ValueError(
                f"Cannot offset times with data type: '{times.dtype}' "
                f"using a frequency of type: '{type(freq)}'."
            )
        out = times + n * freq
    elif isinstance(times, pl_Series) and isinstance(freq, int):
        out = times + n * freq
    elif isinstance(times, pl_Series) and isinstance(freq, str):
        total_offset = _multiply_pl_freq(freq, n)
        out = times.dt.offset_by(total_offset)
        out = _ensure_month_ends(out, times, freq)
    else:
        raise ValueError(
            f"Cannot offset times of type: '{type(times)}' "
            f"using a frequency of type: '{type(freq)}'."
        )
    return out

# %% ../nbs/processing.ipynb 42
def offset_dates(
    dates: Union[Series, pd.Index],
    freq: Union[int, str, BaseOffset],
    n: Union[int, Series],
) -> Union[Series, pd.Index]:
    warnings.warn(
        "`offset_dates` has been renamed to `offset_times`", category=DeprecationWarning
    )
    return offset_times(dates, freq, n)

# %% ../nbs/processing.ipynb 43
def time_ranges(
    starts: Union[Series, pd.Index],
    freq: Union[int, str, BaseOffset],
    periods: int,
) -> Series:
    if isinstance(starts, pd.Series):
        starts = pd.Index(starts)
    if isinstance(starts, pd.Index):
        starts_dtype = starts.dtype.type
        if issubclass(starts_dtype, np.integer):
            out = np.hstack(
                [
                    np.arange(start, start + freq * periods, freq, dtype=starts_dtype)
                    for start in starts
                ]
            )
        elif pd.api.types.is_datetime64_dtype(starts_dtype):
            if isinstance(freq, str):
                freq = pd.tseries.frequencies.to_offset(freq)
            out = []
            for i in range(periods):
                out.append([starts + i * freq])
            out = np.vstack(out).ravel(order="F")
        else:
            raise ValueError(
                f"`starts` must be integers or timestamps, got '{starts_dtype}'."
            )
        out = pd.Series(out)
    else:
        try:
            is_int = starts.dtype.is_integer()
        except AttributeError:
            is_int = starts.is_integer()
        if is_int:
            ends = starts + freq * periods
            out = pl.int_ranges(starts, ends, freq, eager=True).explode()
        else:
            ends = offset_times(starts, freq, periods - 1)
            if starts.dtype == pl.Date:
                ranges_fn = pl.date_ranges
            else:
                ranges_fn = pl.datetime_ranges
            out = ranges_fn(starts, ends, interval=freq, eager=True).explode()
            out = _ensure_month_ends(out, starts, freq)
        out = out.alias(starts.name)
    return out

# %% ../nbs/processing.ipynb 46
def repeat(
    s: Union[Series, pd.Index, np.ndarray], n: Union[int, np.ndarray, Series]
) -> Union[Series, pd.Index, np.ndarray]:
    if isinstance(s, pl_Series):
        if isinstance(n, np.ndarray):
            n = pl_Series(n)
        out = (
            pl.DataFrame(s.alias("x"))
            .select(pl.col("x").repeat_by(n))["x"]
            .explode()
            .alias(s.name)
        )
    else:
        out = np.repeat(s, n)
        if isinstance(out, pd.Series):
            out = out.reset_index(drop=True)
    return out

# %% ../nbs/processing.ipynb 49
def cv_times(
    times: np.ndarray,
    uids: Union[Series, pd.Index],
    indptr: np.ndarray,
    h: int,
    test_size: int,
    step_size: int,
    id_col: str = "unique_id",
    time_col: str = "ds",
) -> DataFrame:
    if test_size < h:
        raise ValueError("`test_size` should be greater or equal to `h`.")
    n, resid = divmod(test_size - h, step_size)
    if resid != 0:
        raise ValueError("`test_size - h` should be a multiple `step_size`")
    n_windows = n + 1
    if isinstance(uids, pl_Series):
        df_constructor = pl_DataFrame
    else:
        df_constructor = pd.DataFrame
    sizes = np.diff(indptr)
    out_times = []
    out_cutoffs = []
    out_ids = []
    for i in range(n_windows):
        offset = test_size - i * step_size + 1
        use_series = sizes >= offset
        cutoff_idxs = indptr[1:][use_series] - offset
        valid_idxs = np.repeat(cutoff_idxs + 1, h) + np.tile(
            np.arange(h), cutoff_idxs.size
        )
        out_times.append(times[valid_idxs])
        out_cutoffs.append(np.repeat(times[cutoff_idxs], h))
        if isinstance(uids, pl_Series):
            use_series = pl_Series(use_series)
        out_ids.append(repeat(filter_with_mask(uids, use_series), h))
    return df_constructor(
        {
            id_col: vertical_concat(out_ids),
            time_col: np.hstack(out_times),
            "cutoff": np.hstack(out_cutoffs),
        }
    )

# %% ../nbs/processing.ipynb 51
def group_by(df: Union[Series, DataFrame], by, maintain_order=False):
    if isinstance(df, (pd.Series, pd.DataFrame)):
        out = df.groupby(by, observed=True, sort=not maintain_order)
    else:
        if isinstance(df, pl_Series):
            df = df.to_frame()
        try:
            out = df.group_by(by, maintain_order=maintain_order)
        except AttributeError:
            out = df.groupby(by, maintain_order=maintain_order)
    return out

# %% ../nbs/processing.ipynb 52
def group_by_agg(df: DataFrame, by, aggs, maintain_order=False) -> DataFrame:
    if isinstance(df, pd.DataFrame):
        out = group_by(df, by, maintain_order).agg(aggs).reset_index()
    else:
        out = group_by(df, by, maintain_order).agg(
            *[getattr(pl.col(c), agg)() for c, agg in aggs.items()]
        )
    return out

# %% ../nbs/processing.ipynb 55
def is_in(s: Series, collection) -> Series:
    if isinstance(s, pl_Series):
        out = s.is_in(collection)
    else:
        out = s.isin(collection)
    return out

# %% ../nbs/processing.ipynb 58
def between(s: Series, lower: Series, upper: Series) -> Series:
    if isinstance(s, pd.Series):
        out = s.between(lower, upper)
    else:
        out = s.is_between(lower, upper)
    return out

# %% ../nbs/processing.ipynb 61
def fill_null(df: DataFrame, mapping: Dict[str, Any]) -> DataFrame:
    if isinstance(df, pd.DataFrame):
        out = df.fillna(mapping)
    else:
        out = df.with_columns(*[pl.col(col).fill_null(v) for col, v in mapping.items()])
    return out

# %% ../nbs/processing.ipynb 64
def cast(s: Series, dtype: type) -> Series:
    if isinstance(s, pd.Series):
        s = s.astype(dtype)
    else:
        s = s.cast(dtype)
    return s

# %% ../nbs/processing.ipynb 67
def value_cols_to_numpy(
    df: DataFrame, id_col: str, time_col: str, target_col: str
) -> np.ndarray:
    exclude_cols = [id_col, time_col, target_col]
    value_cols = [target_col] + [col for col in df.columns if col not in exclude_cols]
    data = to_numpy(df[value_cols])
    if data.dtype not in (np.float32, np.float64):
        data = data.astype(np.float32)
    return data

# %% ../nbs/processing.ipynb 68
def make_future_dataframe(
    uids: Series,
    last_times: Union[Series, pd.Index],
    freq: Union[int, str, BaseOffset],
    h: int,
    id_col: str = "unique_id",
    time_col: str = "ds",
) -> DataFrame:
    starts = offset_times(last_times, freq, 1)
    if isinstance(uids, pl_Series):
        df_constructor = pl_DataFrame
    else:
        df_constructor = pd.DataFrame
    return df_constructor(
        {
            id_col: repeat(uids, h),
            time_col: time_ranges(starts, freq=freq, periods=h),
        }
    )

# %% ../nbs/processing.ipynb 71
def anti_join(df1: DataFrame, df2: DataFrame, on: Union[str, List[str]]) -> DataFrame:
    if isinstance(df1, pd.DataFrame) and isinstance(df2, pd.DataFrame):
        out = df1.merge(df2, on=on, how="left", indicator=True)
        out = out[out["_merge"] == "left_only"].drop(columns="_merge")
        out = out.reset_index(drop=True)
    elif isinstance(df1, pl_DataFrame) and isinstance(df2, pl_DataFrame):
        out = join(df1, df2, on=on, how="anti")
    else:
        raise ValueError(
            "df1 and df2 must be pandas or polars dataframes of the same type. "
            f"Got type(df1): '{type(df1)}', type(df2): '{type(df2)}'"
        )
    return out

# %% ../nbs/processing.ipynb 74
def process_df(
    df: DataFrame, id_col: str, time_col: str, target_col: str
) -> Tuple[Series, np.ndarray, np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """Extract components from dataframe

    Parameters
    ----------
    df : pandas or polars DataFrame
        Input dataframe with id, times and target values.

    Returns
    -------
    ids : pandas or polars Serie
        serie with the sorted unique ids present in the data.
    last_times : numpy array
        array with the last time for each serie.
    data : numpy ndarray
        2d array with target plus features values.
    indptr : numpy ndarray
        1d array with indices to the start and end of each serie.
    sort_idxs : numpy array or None
        array with the indices that would sort the original data.
        If the data is already sorted this is `None`.
    """
    # validations
    validate_format(df, id_col, time_col, target_col)

    # ids
    id_counts = counts_by_id(df, id_col)
    uids = id_counts[id_col]

    # indices
    sizes = id_counts["counts"].to_numpy()
    indptr = np.append(0, sizes.cumsum()).astype(np.int32)
    last_idxs = indptr[1:] - 1

    # data
    data = value_cols_to_numpy(df, id_col, time_col, target_col)

    # check if we need to sort
    sort_idxs = maybe_compute_sort_indices(df, id_col, time_col)
    if sort_idxs is not None:
        data = data[sort_idxs]
        last_idxs = sort_idxs[last_idxs]
    times = df[time_col].to_numpy()[last_idxs]
    return uids, times, data, indptr, sort_idxs

# %% ../nbs/processing.ipynb 76
class DataFrameProcessor:
    def __init__(
        self,
        id_col: str = "unique_id",
        time_col: str = "ds",
        target_col: str = "y",
    ):
        self.id_col = id_col
        self.time_col = time_col
        self.target_col = target_col

    def process(
        self, df: DataFrame
    ) -> Tuple[Series, np.ndarray, np.ndarray, np.ndarray, Optional[np.ndarray]]:
        return process_df(df, self.id_col, self.time_col, self.target_col)

# %% ../nbs/processing.ipynb 80
def _single_split(
    df: DataFrame,
    i_window: int,
    n_windows: int,
    h: int,
    id_col: str,
    time_col: str,
    freq: Union[int, str, pd.offsets.BaseOffset],
    max_dates: Series,
    step_size: Optional[int] = None,
    input_size: Optional[int] = None,
) -> Tuple[DataFrame, Series, Series]:
    if step_size is None:
        step_size = h
    test_size = h + step_size * (n_windows - 1)
    offset = test_size - i_window * step_size
    train_ends = offset_times(max_dates, freq, -offset)
    valid_ends = offset_times(train_ends, freq, h)
    train_mask = df[time_col].le(train_ends)
    valid_mask = df[time_col].gt(train_ends) & df[time_col].le(valid_ends)
    if input_size is not None:
        train_starts = offset_times(train_ends, freq, -input_size)
        train_mask &= df[time_col].gt(train_starts)
    if isinstance(train_mask, pd.Series):
        train_sizes = train_mask.groupby(df[id_col], observed=True, sort=False).sum()
        train_sizes = train_sizes.reset_index()
    else:
        tmp_df = pl.DataFrame({id_col: df[id_col], time_col: train_mask})
        train_sizes = group_by_agg(
            tmp_df, id_col, {time_col: "sum"}, maintain_order=True
        )
    zeros_mask = train_sizes[time_col].eq(0)
    if zeros_mask.all():
        raise ValueError(
            "All series are too short for the cross validation settings, "
            f"at least {offset + 1} samples are required.\n"
            "Please reduce `n_windows` or `h`."
        )
    elif zeros_mask.any():
        ids = filter_with_mask(train_sizes[id_col], zeros_mask)
        warnings.warn(
            "The following series are too short for the window "
            f"and will be dropped: {reprlib.repr(list(ids))}"
        )
        dropped_ids = is_in(df[id_col], ids)
        valid_mask &= ~dropped_ids
    if isinstance(train_ends, pd.Series):
        cutoffs: DataFrame = (
            train_ends.set_axis(df[id_col])
            .groupby(id_col, observed=True)
            .head(1)
            .rename("cutoff")
            .reset_index()
        )
    else:
        cutoffs = train_ends.to_frame().with_columns(df[id_col])
        cutoffs = (
            group_by(cutoffs, id_col)
            .agg(pl.col(time_col).head(1))
            .explode(pl.col(time_col))
            .rename({time_col: "cutoff"})
        )
    return cutoffs, train_mask, valid_mask

# %% ../nbs/processing.ipynb 81
def backtest_splits(
    df: DataFrame,
    n_windows: int,
    h: int,
    id_col: str,
    time_col: str,
    freq: Union[int, str, pd.offsets.BaseOffset],
    step_size: Optional[int] = None,
    input_size: Optional[int] = None,
) -> Generator[Tuple[DataFrame, DataFrame, DataFrame], None, None]:
    if isinstance(df, pd.DataFrame):
        max_dates = df.groupby(id_col, observed=True)[time_col].transform("max")
    else:
        max_dates = df.select(pl.col(time_col).max().over(id_col))[time_col]
    for i in range(n_windows):
        cutoffs, train_mask, valid_mask = _single_split(
            df,
            i_window=i,
            n_windows=n_windows,
            h=h,
            id_col=id_col,
            time_col=time_col,
            freq=freq,
            max_dates=max_dates,
            step_size=step_size,
            input_size=input_size,
        )
        train = filter_with_mask(df, train_mask)
        valid = filter_with_mask(df, valid_mask)
        yield cutoffs, train, valid

# %% ../nbs/processing.ipynb 85
def add_insample_levels(
    df: DataFrame,
    models: List[str],
    level: List[Union[int, float]],
    id_col: str = "unique_id",
    target_col: str = "y",
) -> DataFrame:
    import operator

    from scipy.stats import norm

    df = copy_if_pandas(df, deep=False)
    cuts = norm.ppf(0.5 + np.asarray(level) / 200).reshape(1, -1)
    if isinstance(df, pd.DataFrame):
        errors = df[models].sub(df[target_col], axis=0)
        stds = errors.groupby(df[id_col], observed=True).transform("std")
    else:
        exprs = (pl.col(m).sub(pl.col(target_col)).std().over(id_col) for m in models)
        stds = df.select(exprs)
    stds = to_numpy(stds)
    preds = to_numpy(df[models])
    vals = np.empty_like(preds, shape=(preds.shape[0], len(models) * 2 * len(level)))
    cols = []
    k = 0
    for i, model in enumerate(models):
        widths = cuts * stds[:, [i]]
        for side, op in {"lo": operator.sub, "hi": operator.add}.items():
            for j, lvl in enumerate(level):
                cols.append(f"{model}-{side}-{lvl}")
                vals[:, k] = op(preds[:, i], widths[:, j])
                k += 1
    return assign_columns(df, cols, vals)
