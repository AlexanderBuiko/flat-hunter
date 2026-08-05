# Real-world indirect prompt-injection cases

Week-10 (Security), Day 12 — *reinforcement* step. Three documented attacks on
shipping products, each the same shape as flat-hunter's exposure: an LLM agent reads
**content it did not author** (a web page, a shared document, a repository) and is
steered by an instruction hidden inside that content. The user asks for something
ordinary; the agent quietly does the attacker's bidding.

The [simplified reproduction](reproduction-bing-case.md) adapts the first case (Bing —
hidden text on a page the agent reads) to flat-hunter's own scraper.

---

## 1. Bing Chat — hidden text on a web page (Greshake et al., Feb 2023)

**What happened.** The first systematic demonstration of *indirect* prompt injection.
Bing Chat (GPT-4-powered) could read the web page open in the user's browser tab. A
page carrying injected instructions — including as small or hidden text — took over the
conversation: the chatbot changed persona, pushed a scam link, or tried to elicit the
user's real name and send it onward. The same technique hit code-completion engines.

**Carrier.** Ordinary (and hidden) text on a web page the model retrieves as context.

**Why it works.** To the model there is no boundary between "the user's question" and
"the page content" — it is one token stream, and the injected instruction, arriving in
retrieved data, is followed like any other. The user never sees the instruction.

**Defence.** Treat retrieved content as untrusted data (boundary markers); sanitise
hidden/'invisible' text before it reaches the model; validate the output; keep the model
away from tools that can act without a human check.

*Sources:* [arXiv 2302.12173](https://arxiv.org/abs/2302.12173) ·
[Kai Greshake — write-up](https://kai-greshake.de/posts/llm-malware/)

---

## 2. Google Bard — injection via a shared Google Doc → data exfiltration (Rehberger / rez0 / Greshake, Nov 2023)

**What happened.** Bard's Extensions gave it access to the user's Google Drive, Docs and
Gmail. A **Google Doc shared with the victim** carried injected instructions: encode the
user's prior conversation into a URL and render it as a markdown **image**. Bard rendered
the image, which fired an automatic request to an attacker endpoint — exfiltrating the
chat with **no click** from the user. Google's CSP limited image hosts to `*.google.com`,
so the researchers ran the exfiltration endpoint on `script.google.com` via Google
AppScript. Reported to Google 19 Sep 2023; fixed about a month later.

**Carrier.** A shared document (untrusted data) + a markdown-image render as the
exfiltration channel.

**Why it works.** Two failures chained: the doc's text was trusted as instructions
(injection), and the client auto-rendered an attacker-controlled image URL (an output
side effect that leaks data). CSP alone was not enough.

**Defence.** Sanitise document content; do not auto-render model-produced image/URLs to
attacker hosts (output guard); strip/deny outbound URLs in responses; human-in-the-loop
for anything that reaches an external endpoint.

*Sources:*
[Embrace The Red](https://embracethered.com/blog/posts/2023/google-bard-data-exfiltration/) ·
[Simon Willison](https://simonwillison.net/2023/Nov/4/hacking-google-bard-from-prompt-injection-to-data-exfiltration/)

---

## 3. GitHub Copilot — instructions hidden in repository content

**What happened.** Several variants, all "the assistant reads the repo and obeys text
hidden in it":
- **Invisible-Unicode instruction files** — a PoC hides a payload inside
  `copilot-instructions.md` using invisible Unicode *tag* characters (`U+E0000–U+E007F`):
  invisible to a human reviewing the file, fully read by the model.
- **RoguePilot (Orca, 2026)** — a malicious **GitHub issue** triggers a passive injection
  when a user opens a Codespace from it; the assistant silently runs the attacker's
  instructions and can leak a privileged `GITHUB_TOKEN`.
- **Hidden payload in a dependency/config file** (Trail of Bits demo) — the generated app
  ships a backdoor that takes commands via an `X-Backdoor-Cmd` HTTP header.
- **Markdown HTML comments** (`<!-- … -->`) — GitHub renders them invisibly, so
  instructions hide in READMEs and issues in plain sight.

**Carrier.** Repository files, instruction files, issues — content the coding agent reads
and trusts.

**Why it works.** The assistant treats repo content as authoritative context; hidden
Unicode / HTML comments defeat human review; the agent has real power (write code, read
tokens), so an obeyed instruction becomes code execution or secret theft.

**Defence.** Strip invisible Unicode and HTML comments from any file before it enters the
prompt; boundary-mark repo content as data; never let repo-derived text reach a
privileged action without a human gate; scan generated code for injected side effects.

*Sources:*
[Unicode-injection PoC](https://github.com/0x6f677548/copilot-instructions-unicode-injection) ·
[RoguePilot — Orca](https://orca.security/resources/blog/roguepilot-github-copilot-vulnerability/) ·
[Trail of Bits](https://blog.trailofbits.com/2025/08/06/prompt-injection-engineering-for-attackers-exploiting-github-copilot/)

---

## How these map to flat-hunter

| Real case | flat-hunter analogue |
|---|---|
| Bing — hidden text on a scraped **web page** | the scraper reads realt.by **listing pages**; a listing's `description` is the hidden-text channel — **reproduced** in [reproduction-bing-case.md](reproduction-bing-case.md) |
| Bard — exfiltration via rendered **URL/image** | a summary carrying an attacker URL/contact → flat-hunter's L3 drops URLs/contacts from `summary` |
| Copilot — **invisible Unicode / HTML comments** in read content | the same carriers in a listing → flat-hunter's L1 strips HTML comments, tags and zero-width/invisible Unicode before the model |

The lesson shared by all three: **content the agent reads is not a command.** flat-hunter
applies that with the three layers documented in
[indirect-injection-report.md](indirect-injection-report.md).
