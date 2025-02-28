from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

from pandas import DataFrame

from freqtrade.enums import CandleType
from freqtrade.exceptions import OperationalException
from freqtrade.strategy.strategy_helper import merge_informative_pair


PopulateIndicators = Callable[[Any, DataFrame, dict], DataFrame]


@dataclass
class InformativeData:
    asset: Optional[str]
    timeframe: str
    fmt: Union[str, Callable[[Any], str], None]
    ffill: bool
    candle_type: Optional[CandleType]
    startup_candle_count: int


def informative(timeframe: str, asset: str = '',
                fmt: Optional[Union[str, Callable[[Any], str]]] = None,
                *,
                candle_type: Optional[Union[CandleType, str]] = None,
                ffill: bool = True,
                startup_candle_count: int = 0,
                analyze_per_epoch: bool = False
                ) -> Callable[[PopulateIndicators], PopulateIndicators]:
    """
    A decorator for populate_indicators_Nn(self, dataframe, metadata), allowing these functions to
    define informative indicators.

    Example usage:

        @informative('1h')
        def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
            return dataframe

    :param timeframe: Informative timeframe. Must always be equal or higher than strategy timeframe.
    :param asset: Informative asset, for example BTC, BTC/USDT, ETH/BTC. Do not specify to use
    current pair.
    :param fmt: Column format (str) or column formatter (callable(name, asset, timeframe)). When not
    specified, defaults to:
    * {base}_{quote}_{column}_{timeframe} if asset is specified.
    * {column}_{timeframe} if asset is not specified.
    Format string supports these format variables:
    * {asset} - full name of the asset, for example 'BTC/USDT'.
    * {base} - base currency in lower case, for example 'eth'.
    * {BASE} - same as {base}, except in upper case.
    * {quote} - quote currency in lower case, for example 'usdt'.
    * {QUOTE} - same as {quote}, except in upper case.
    * {column} - name of dataframe column.
    * {timeframe} - timeframe of informative dataframe.
    :param ffill: ffill dataframe after merging informative pair.
    :param candle_type: '', mark, index, premiumIndex, or funding_rate
    """
    _asset = asset
    _timeframe = timeframe
    _fmt = fmt
    _ffill = ffill
    _candle_type = CandleType.from_string(candle_type) if candle_type else None
    _startup_candle_count = startup_candle_count

    def decorator(fn: PopulateIndicators):
        informative_pairs = getattr(fn, '_ft_informative', [])
        informative_pairs.append(InformativeData(_asset, _timeframe, _fmt, _ffill, _candle_type,
                                                 _startup_candle_count))
        setattr(fn, '_ft_informative', informative_pairs)
        return fn
    return decorator


def _format_pair_name(config, pair: str) -> str:
    return pair.format(stake_currency=config['stake_currency'],
                       stake=config['stake_currency']).upper()


def _create_and_merge_informative_pair(strategy, dataframe: DataFrame, metadata: dict,
                                       inf_data: InformativeData,
                                       populate_indicators: PopulateIndicators):
    asset = inf_data.asset or ''
    timeframe = inf_data.timeframe
    fmt = inf_data.fmt
    candle_type = inf_data.candle_type
    startup_candle_count = inf_data.startup_candle_count

    config = strategy.config

    if asset:
        # Insert stake currency if needed.
        asset = _format_pair_name(config, asset)
    else:
        # Not specifying an asset will define informative dataframe for current pair.
        asset = metadata['pair']

    market = strategy.dp.market(asset)
    if market is None:
        raise OperationalException(f'Market {asset} is not available.')
    base = market['base']
    quote = market['quote']

    # Default format. This optimizes for the common case: informative pairs using same stake
    # currency. When quote currency matches stake currency, column name will omit base currency.
    # This allows easily reconfiguring strategy to use different base currency. In a rare case
    # where it is desired to keep quote currency in column name at all times user should specify
    # fmt='{base}_{quote}_{column}_{timeframe}' format or similar.
    if not fmt:
        fmt = '{column}_{timeframe}'                # Informatives of current pair
        if inf_data.asset:
            fmt = '{base}_{quote}_' + fmt           # Informatives of other pairs

    inf_metadata = {'pair': asset, 'timeframe': timeframe}
    inf_dataframe = strategy.dp.get_pair_dataframe(asset, timeframe,
                                                   candle_type, startup_candle_count)
    inf_dataframe = populate_indicators(strategy, inf_dataframe, inf_metadata)

    formatter: Any = None
    if callable(fmt):
        formatter = fmt             # A custom user-specified formatter function.
    else:
        formatter = fmt.format      # A default string formatter.

    fmt_args = {
        'BASE': base.upper(),
        'QUOTE': quote.upper(),
        'base': base.lower(),
        'quote': quote.lower(),
        'asset': asset,
        'timeframe': timeframe,
    }
    inf_dataframe.rename(columns=lambda column: formatter(column=column, **fmt_args),
                         inplace=True)

    date_column = formatter(column='date', **fmt_args)
    if date_column in dataframe.columns:
        raise OperationalException(f'Duplicate column name {date_column} exists in '
                                   f'dataframe! Ensure column names are unique!')
    dataframe = merge_informative_pair(dataframe, inf_dataframe, strategy.timeframe, timeframe,
                                       ffill=inf_data.ffill, append_timeframe=False,
                                       date_column=date_column)
    return dataframe
