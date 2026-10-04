// What a thinking block says: its text, else the readable summaries in its provider data (OpenRouter keeps them in
// the block's signature as reasoning_details), else a note that the model sent its reasoning encrypted (OpenAI's
// models through OpenRouter often do), so the page says why there is nothing to read rather than show nothing. The
// main agent's (worker.ts) and a subagent's (router.ts) alike.
export function thoughtOf(block: any): string {
	if (block?.thinking?.trim()) return block.thinking;
	try {
		const details = JSON.parse(block?.thinkingSignature ?? "");
		const text = (Array.isArray(details) ? details : [details]).map((d: any) => d?.summary ?? d?.text ?? "").filter(Boolean).join("\n\n");
		if (text.trim()) return text;
	} catch {}
	return "(The model kept this reasoning private: it sent it encrypted, with no readable summary.)";
}
