/** The shared rules for listing security-scan findings on a consent surface.
 *
 *  🔑 BOTH CONSENT SURFACES TRUNCATE THE SAME LIST, and each had hardcoded its own `8`. An app
 *  install (`apps/installConsent`) and a skill install (`skills/MarketplaceDetail`) render findings
 *  from the same `scan.findings` shape for the same decision — "do I trust this?" — so the cap and
 *  the sentence that discloses it belong in one place. Two independently-chosen limits are two
 *  numbers that drift, and a user comparing the two surfaces has no way to know either is a limit.
 *
 *  🪤 THE CAP IS NOT THE BUG; THE SILENCE IS. `installConsent`'s own comment says the surface exists
 *  so "the scanner findings and the 'Install anyway' action are reachable without re-typing the
 *  source", and that a warning verdict "must show the same scanner findings" wherever the install
 *  started. A list that stops at 8 with nothing said contradicts that: the user reads eight findings
 *  as ALL the findings and consents to an app with fourteen. */

/** How many findings a consent surface lists before it says how many it is hiding. */
export const SCAN_FINDINGS_SHOWN = 8

/** The sentence a truncated findings list owes the user, or `null` when nothing is hidden. */
export function hiddenFindingsNote(total: number): string | null {
  const hidden = total - SCAN_FINDINGS_SHOWN
  if (hidden <= 0) return null
  return `+${hidden} more finding${hidden === 1 ? '' : 's'} not shown`
}

/** What each scanner rule MEANS, in terms of what the scanned code can then do to this machine.
 *
 *  🔑 A RULE NAME IS NOT A DISCLOSURE. The findings list rendered `python_exec (warning) —
 *  server/provider.py: subprocess.run([...])`, which names the pattern and shows the code but
 *  never says what the pattern lets it do. A non-expert cannot weigh "python_exec" at all,
 *  and the whole point of the surface is a yes/no a non-expert can give. The gloss COMPLEMENTS
 *  the evidence — the evidence is the concrete argv, this is the consequence — so it stays one
 *  clause and never restates the snippet.
 *
 *  🪤 It is disclosure only. Glossing a rule does not soften it: the severity beside it and the
 *  verdict above it are untouched, and a `dangerous` finding reads as terminal either way. The
 *  vocabulary is closed (`supply_chain.py`'s pattern catalog), and a Python rail asserts this map
 *  covers every rule the scanner can emit — a new rule ships glossed or reds the suite, because
 *  the failure mode here is silence, and silence is what the user reads as "probably fine".
 *
 *  🪤 THE SUBJECT IS "THIS CODE", NOT "THE APP" — and that is load-bearing, not style. Every
 *  sentence here said "The app …" while the map had one consumer. Wiring the skills marketplace
 *  in (#2535) rendered "The app runs an external program on your machine." on a SKILL install
 *  card: one map, two nouns, and the wrong one on the newer surface. One map answering "what does
 *  this rule mean" cannot name one of the surfaces that asks. A finding is located in a FILE
 *  (`f.path`) either way, so the code is the honest subject on both. Any third consumer inherits
 *  a sentence that is already true for it. */
export const SCAN_RULE_GLOSS: Record<string, string> = {
  // Terminal (dangerous) content.
  destructive_root: 'This code deletes files from the root of the filesystem or your home directory.',
  fork_bomb: 'This code spawns processes without limit until the machine stops responding.',
  disk_wipe: 'This code writes straight to a raw disk device, destroying what is on it.',
  remote_exec_pipe: 'This code downloads more code from the internet and runs it immediately, unread.',
  obfuscated_exec: 'This code decodes hidden text and runs it, so what runs cannot be read here.',
  exfil_sensitive_path: 'This code reads a credential file and sends its contents off this machine.',
  bidi_override: 'Direction-flipping characters hide text here, so what you read is not what runs.',
  // Overridable (warning) content.
  eval_exec: 'This code builds more code as text while it runs, then executes it.',
  pipe_to_shell: 'This code feeds output straight into a shell to be run as commands.',
  curl_network: 'This code downloads from the internet while it runs.',
  sudo_use: 'This code asks for administrator rights to act as root on this machine.',
  python_exec: 'This code runs an external program on your machine.',
  crontab_write: "This code edits this machine's scheduled-job table, so it can keep running later.",
  reads_sensitive_path: 'This code reads a file where credentials and keys are kept.',
  zero_width_chars: 'Invisible characters are present, which can hide text from a reviewer.',
  // Prompt injection — prose aimed at the assistant that reads the bundle's own text.
  injection_ignore: 'Text here addresses your assistant and tells it to ignore its own instructions.',
  injection_disregard: 'Text here addresses your assistant and tells it to disregard what it was told.',
  injection_coerce: 'Text here addresses your assistant and orders it to run or call something.',
  injection_override: "Text here poses as a replacement for your assistant's instructions.",
}

/** The plain-language sentence for a scanner rule, or `''` for a rule this build has no gloss
 *  for. Returning empty rather than echoing the rule name keeps the row honest: a name repeated
 *  as though it were an explanation is the defect, not the fix. */
export function ruleGloss(rule: string): string {
  return SCAN_RULE_GLOSS[rule] ?? ''
}
