import { createRoot } from "react-dom/client";
import Chat from "./components/Chat";
import ModelPicker from "./components/ModelPicker";
import TelegramCard from "./components/TelegramCard";

const mode = location.hash.slice(1) || "picker";
const el = document.getElementById("root")!;
if (mode.startsWith("chat")) {
  createRoot(el).render(<Chat />);
} else if (mode.startsWith("telegram")) {
  createRoot(el).render(
    <main className="view">
      <div className="dashboard-grid">
        <TelegramCard />
      </div>
    </main>,
  );
} else {
  createRoot(el).render(<ModelPicker onClose={() => {}} onSaved={() => {}} />);
}
