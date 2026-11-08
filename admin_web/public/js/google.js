import { notice } from "./ui.js";
export async function mount(root) { root.replaceChildren(notice("info", "This tab is still being moved over — use the old dashboard for now.")); }
