import { createRoot } from "react-dom/client";
import Chat from "./components/Chat";
import ModelPicker from "./components/ModelPicker";

const mode = location.hash.slice(1) || "picker";
const el = document.getElementById("root")!;
if (mode.startsWith("chat")) {
  createRoot(el).render(<Chat />);
} else {
  createRoot(el).render(<ModelPicker onClose={() => {}} onSaved={() => {}} />);
}
