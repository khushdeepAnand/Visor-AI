import { render, screen } from "@testing-library/react";
import { VirtualOrderTable } from "@/components/VirtualOrderTable";

describe("VirtualOrderTable", () => {
  it("renders a virtualized order table contract", () => {
    render(<VirtualOrderTable orders={[{ id: 1, symbol: "RELIANCE", side: "BUY", quantity: 1, price: 2500, status: "FILLED", order_type: "MARKET" }]} />);
    expect(screen.getByTestId("virtual-order-table")).toBeInTheDocument();
    expect(screen.getByText("RELIANCE")).toBeInTheDocument();
    expect(screen.getByText("FILLED")).toBeInTheDocument();
  });
});
