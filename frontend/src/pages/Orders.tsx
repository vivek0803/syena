import { ErrorNote, Page, Panel, Pill, Table } from "../components/ui";
import { money, when } from "../lib/format";
import { useApi } from "../lib/hooks";

const statusTone: Record<string, string> = { COMPLETE: "gain", PLACED: "info", PENDING: "mute", REJECTED: "loss", FAILED: "loss", CANCELLED: "warn" };

export default function Orders() {
  const { data, error } = useApi<any[]>("/api/orders?limit=300", { refetchInterval: 20_000 });
  return (
    <Page title="Orders" intro="Every order sent to the paper simulator or Zerodha, with the broker's exact messages, including rejections.">
      <ErrorNote error={error} />
      <Panel pad={false}>
        <Table rows={data ?? []} rowKey={(r: any) => r.id} cols={[
          { key: "created_at", label: "Time", render: (r: any) => when(r.created_at) },
          { key: "symbol", label: "Stock", render: (r: any) => <b>{r.symbol}</b> },
          { key: "purpose", label: "For", render: (r: any) => r.purpose.toLowerCase() },
          { key: "side", label: "Side" },
          { key: "qty", label: "Qty", align: "right" },
          { key: "order_type", label: "Type", render: (r: any) => `${r.order_type.toLowerCase()}${r.price ? ` @ ${r.price}` : ""} (${r.product})` },
          { key: "broker", label: "Venue", render: (r: any) => <Pill tone={r.broker === "kite" ? "loss" : "gain"}>{r.broker === "kite" ? "Zerodha" : "paper"}</Pill> },
          { key: "status", label: "Status", render: (r: any) => <Pill tone={statusTone[r.status]}>{r.status.toLowerCase()}</Pill> },
          { key: "fill_price", label: "Fill", align: "right", render: (r: any) => money(r.fill_price) },
          { key: "charges", label: "Charges", align: "right", render: (r: any) => money(r.charges) },
          { key: "message", label: "Broker message", className: "whitespace-normal min-w-[240px] text-parch-dim", render: (r: any) => `${r.message ?? ""}${r.attempts > 1 ? ` (${r.attempts} attempts)` : ""}` },
          { key: "idempotency_key", label: "Idempotency key", className: "text-[12px] text-mute" },
        ]} />
      </Panel>
    </Page>
  );
}
