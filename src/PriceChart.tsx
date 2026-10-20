import TechnicalChart from './TechnicalChart';
import type { ChartOverlay, ChartStyle } from './chartMath';
import type { Candle, News } from './types';
import type { IndicatorSelection } from './indicatorCatalog';

export default function PriceChart(props: {
  candles: Candle[]; news: News[]; mode: string; period: string;
  selection: IndicatorSelection; onSelectionChange: (value: IndicatorSelection) => void;
  chartStyle?: ChartStyle; overlay?: ChartOverlay; onEvent: (id: string) => void;
}) {
  return <TechnicalChart {...props} />;
}
