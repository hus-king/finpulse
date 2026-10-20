import TechnicalChart from './TechnicalChart';
import type { buildIntraday } from './intradayMath';
import type { Candle } from './types';
import type { IndicatorSelection } from './indicatorCatalog';

type Session = ReturnType<typeof buildIntraday>;
export default function IntradayChart({ session, candles, loading, selection, onSelectionChange }: {
  session: Session; candles: Candle[]; loading: boolean;
  selection: IndicatorSelection; onSelectionChange: (value: IndicatorSelection) => void;
}) {
  return <div className="intraday-chart">
    <TechnicalChart candles={candles} session={session} mode="当日" period="分时" selection={selection} onSelectionChange={onSelectionChange} />
    {!session.rows.length && <div className="intraday-empty" role="status"><strong>{loading ? '正在获取分钟行情' : '该交易日尚无分钟数据'}</strong><span>{session.day ?? '等待交易日确认'} · 已保留完整交易时间轴</span></div>}
    <div className="intraday-axis-note">09:30 — 15:00 · 午间休市折叠{session.previousClose != null ? ` · 参考收盘价 ${session.previousClose.toFixed(2)}` : ''}</div>
  </div>;
}
