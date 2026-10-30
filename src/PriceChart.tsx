import TechnicalChart from './TechnicalChart';
import type { ChartOverlay, ChartStyle } from './chartMath';
import type { Candle } from './types';
import type { IndicatorSelection } from './indicatorCatalog';

export default function PriceChart(props: {
  instrument?: string; volumeUnit?: string; candles: Candle[]; mode: string; period: string;
  selection: IndicatorSelection; onSelectionChange: (value: IndicatorSelection) => void;
  chartStyle?: ChartStyle; overlay?: ChartOverlay;
}) {
  return <TechnicalChart {...props} />;
}
