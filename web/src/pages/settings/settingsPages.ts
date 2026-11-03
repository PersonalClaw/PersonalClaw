/** Settings' pages, and which sections each one holds (#5897).
 *
 *  Settings had a page per section, forty of them, and the overview a card per page. That is too
 *  many to take in, and several held one switch. So a section now lives on the page it belongs with,
 *  each fold answering one question: who can reach me and can I cut them off (Devices & Access),
 *  what the workspace does on its own (Automations), how this machine is (Health). Seventeen pages
 *  in three groups, one card each on the overview.
 *
 *  A section keeps its id, its panel and its old address: `settings/<section>` is a row of
 *  `app/routeRedirects.ts` that opens `settings/<page>?section=<section>`, and the page scrolls to
 *  that section and focuses its heading. A page's id is the id of the section it is named for where
 *  there is one, so the links the gateway writes (`settings/models`, `settings/durability`) open
 *  their page directly.
 *
 *  Pure data, no imports: the route tests read it without a DOM. */

export const SETTINGS_GROUPS = ['General', 'AI & Automation', 'Security & System'] as const
export type SettingsGroup = (typeof SETTINGS_GROUPS)[number]

export interface SettingsPageDef {
  /** The page's address, `settings/<id>`. */
  readonly id: string
  readonly label: string
  readonly group: SettingsGroup
  /** The columns its card asks for on the overview: 2 where its content needs the width. */
  readonly span: 1 | 2
  /** Its sections, in page order. The first section a page is named for leads. */
  readonly sections: readonly string[]
}

export const SETTINGS_PAGES: readonly SettingsPageDef[] = [
  // ── General ──
  { id: 'design', label: 'Appearance', group: 'General', span: 2, sections: ['design', 'buddy'] },
  { id: 'account', label: 'Account', group: 'General', span: 1, sections: ['account'] },
  { id: 'chat', label: 'Chat', group: 'General', span: 1, sections: ['chat', 'archive'] },
  { id: 'notifications', label: 'Notifications & Inbox', group: 'General', span: 1, sections: ['notifications', 'inbox'] },
  { id: 'voice', label: 'Speech & Transcription', group: 'General', span: 1, sections: ['voice'] },
  // ── AI & Automation ──
  { id: 'models', label: 'Models & Providers', group: 'AI & Automation', span: 2, sections: ['providers', 'models', 'routing', 'search'] },
  { id: 'agent', label: 'Agent', group: 'AI & Automation', span: 1, sections: ['agent', 'prompts', 'tool-output', 'legibility'] },
  { id: 'evals', label: 'Evaluations & Feedback', group: 'AI & Automation', span: 1, sections: ['evals', 'feedback'] },
  { id: 'automations', label: 'Automations', group: 'AI & Automation', span: 2, sections: ['workflows', 'loops', 'sources', 'ambient'] },
  { id: 'apps', label: 'Apps & Packs', group: 'AI & Automation', span: 2, sections: ['apps', 'packs'] },
  { id: 'usage', label: 'Usage', group: 'AI & Automation', span: 1, sections: ['usage'] },
  // ── Security & System ──
  { id: 'security', label: 'Security', group: 'Security & System', span: 1, sections: ['security', 'guardrails', 'audit'] },
  { id: 'devices', label: 'Devices & Access', group: 'Security & System', span: 1, sections: ['devices', 'sender-trust', 'external-access', 'companion'] },
  { id: 'health', label: 'Health', group: 'Security & System', span: 1, sections: ['system', 'doctor', 'diagnostics'] },
  { id: 'secrets', label: 'Secrets', group: 'Security & System', span: 1, sections: ['secrets'] },
  { id: 'durability', label: 'Backups & Data', group: 'Security & System', span: 1, sections: ['durability', 'portability', 'documents'] },
  { id: 'updates', label: 'Updates', group: 'Security & System', span: 1, sections: ['updates'] },
]

/** The page holding section `id`, or undefined for an id no page holds. */
export function pageOfSection(id: string): SettingsPageDef | undefined {
  return SETTINGS_PAGES.find((p) => p.sections.includes(id))
}

/** The page whose address is `settings/<id>`, or undefined. */
export function pageById(id: string): SettingsPageDef | undefined {
  return SETTINGS_PAGES.find((p) => p.id === id)
}

/** Where section `id` opens, after `#/`: its page, and the section when the page holds more than one
 *  and it is not the one the page is named for. */
export function sectionAddress(id: string): string {
  const page = pageOfSection(id)
  if (!page) return `settings/${id}`
  return page.id === id || page.sections.length === 1 ? `settings/${page.id}` : `settings/${page.id}?section=${id}`
}
