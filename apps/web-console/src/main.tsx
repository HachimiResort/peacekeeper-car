import React from "react"
import ReactDOM from "react-dom/client"
import { BrowserRouter } from "react-router-dom"
import { App } from "./app/App"
import { SessionProvider } from "./app/session"
import { FeedbackProvider } from "./app/feedback"
import "./styles.css"

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <FeedbackProvider>
        <SessionProvider>
          <App />
        </SessionProvider>
      </FeedbackProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
