import type { Stock } from './types';

export const headlineIndices = ['sh000001', 'sz399001', 'sh000300'];
export const displayCode = (stock: Stock) => stock.symbol ?? stock.code;
export const isIndex = (stock: Stock) => stock.asset_type === 'index';
