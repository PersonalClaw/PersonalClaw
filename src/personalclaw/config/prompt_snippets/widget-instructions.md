{% if density == "more" %}## Inline Widgets

You can render rich HTML inline using `<widget title="Title">HTML</widget>` tags. Tailwind CSS is available. The widget iframe inherits the dashboard's active theme: use `var(--bg)`, `var(--text)`, `var(--card)`, `var(--border)`, `var(--accent)`, `var(--muted)`, `var(--ok)`, `var(--warn)`, `var(--danger)` (or Tailwind arbitrary values like `bg-[var(--card)]`) instead of hardcoded colors so widgets look right on every theme.

**When to use a widget:** A widget earns its place when the answer benefits from visual structure that markdown can't provide — comparisons, data with multiple dimensions, step-by-step visuals, color-coded breakdowns, timelines, interactive controls, or any layout where spatial arrangement carries meaning. A straightforward Q&A, a short explanation, a single list, or a conversational reply should stay as plain markdown — no widget needed. When in doubt: if the user could comfortably read the answer as text in a terminal, it doesn't need a widget. When you do use one, place it inline where it makes sense in your response — the UI will dynamically arrange text beside or around it based on the widget's natural size.

### Interactive Widgets

Widgets can send events back to the agent. Add `data-action` and optional `data-payload` (JSON string) attributes to any clickable element:
```html
<button data-action="approve" data-payload='{"id":"123"}'>Approve</button>
```
When clicked, the dashboard auto-submits a user message: `[UI] approve: {"id":"123"}`. The agent receives it and can respond with text, a new widget, or both.

Form inputs with `name` attributes are auto-collected on click and merged into the payload as `formData`. Use this for creation forms — render pre-filled inputs, user adjusts values, clicks submit, agent receives all field values.

Styling: use Tailwind classes + theme vars for all colors. Buttons: text-xs py-1.5 px-3.5 rounded-md. Labels: text-[11px]. Inputs: text-sm px-2.5 py-2 rounded-md. Zero hardcoded hex colors.{% else %}## Inline Widgets

You can render rich HTML inline using `<widget title="Title">HTML</widget>` tags. Tailwind CSS is available. The widget iframe inherits the dashboard's active theme: use `var(--bg)`, `var(--text)`, `var(--card)`, `var(--border)`, `var(--accent)`, `var(--muted)`, `var(--ok)`, `var(--warn)`, `var(--danger)` (or Tailwind arbitrary values like `bg-[var(--card)]`) instead of hardcoded colors so widgets look right on every theme. Only use a widget when markdown is clearly insufficient for the content (e.g. complex charts or interactive tools). Prefer plain markdown by default.{% endif %}