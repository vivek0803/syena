import { Link } from "react-router-dom";

/** A stock symbol that opens its chart page. */
export default function StockLink({ symbol, className = "font-semibold" }: { symbol: string; className?: string }) {
  return (
    <Link to={`/stock/${encodeURIComponent(symbol)}`} onClick={(e) => e.stopPropagation()}
      className={`${className} hover:text-saffron hover:underline decoration-saffron/50 underline-offset-2`} title={`Open ${symbol} chart`}>
      {symbol}
    </Link>
  );
}
