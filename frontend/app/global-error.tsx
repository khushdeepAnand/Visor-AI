"use client";

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <html lang="en" className="dark"><body><main style={{minHeight:"100vh",display:"grid",placeItems:"center",background:"#05070d",color:"#f8fafc",padding:24}}><div style={{maxWidth:520,textAlign:"center"}}><h1>StockPilot needs to reload this view.</h1><p style={{color:"#94a3b8"}}>No order was placed. Your saved research remains on the server.</p><button onClick={reset} style={{marginTop:16,padding:"12px 18px",borderRadius:8}}>Reload interface</button></div></main></body></html>;
}
