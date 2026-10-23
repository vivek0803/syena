import type { ISeriesApi, ISeriesPrimitive, ISeriesPrimitivePaneRenderer, ISeriesPrimitivePaneView, SeriesAttachedParameter, Time } from "lightweight-charts";

export type Band = { low: number; high: number; color: string; label: string };  // label: shown on the price axis

/** Horizontal support/resistance zones drawn as shaded bands across the whole chart, behind the candles. */
export class ZoneBands implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<"Candlestick"> | null = null;
  private views: ISeriesPrimitivePaneView[];

  constructor(private bands: Band[]) {
    const self = this;
    const renderer: ISeriesPrimitivePaneRenderer = {
      draw() { /* bands go in the background */ },
      drawBackground(target: any) {
        const s = self.series;
        if (!s) return;
        target.useBitmapCoordinateSpace((scope: any) => {
          const ctx: CanvasRenderingContext2D = scope.context;
          const r = scope.verticalPixelRatio, w = scope.bitmapSize.width;
          ctx.save();
          for (const b of self.bands) {
            const y1 = s.priceToCoordinate(b.high), y2 = s.priceToCoordinate(b.low);
            if (y1 == null || y2 == null) continue;
            const top = Math.min(y1, y2) * r, h = Math.max(Math.abs(y2 - y1) * r, 2 * r);
            ctx.fillStyle = b.color + "26";          // ~15% fill
            ctx.fillRect(0, top, w, h);
            ctx.fillStyle = b.color + "99";          // edges
            ctx.fillRect(0, top, w, Math.max(1, r));
            ctx.fillRect(0, top + h - Math.max(1, r), w, Math.max(1, r));
          }
          ctx.restore();
        });
      },
    };
    this.views = [{ zOrder: () => "bottom", renderer: () => renderer }];
  }

  attached(p: SeriesAttachedParameter<Time>) { this.series = p.series as ISeriesApi<"Candlestick">; }
  detached() { this.series = null; }
  paneViews() { return this.views; }
}
