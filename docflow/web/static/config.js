// DocFlow shared Tailwind configuration — "Paper Archive" theme
// Design tokens from the redesign spec

tailwind.config = {
    darkMode: 'class',
    theme: { extend: {
        colors: {
            // Canvas / surfaces
            "canvas":          "#F4F1EA",
            "sidebar":         "#ECE7DC",
            "panel":           "#EFEADF",
            "card":            "#FFFFFF",
            "inset":           "#F7F5EF",
            "tile":            "#F1ECE0",
            "soft-hover":      "#FBFAF6",

            // Borders
            "border-primary":  "#E6E0D4",
            "border-sidebar":  "#E0D9CB",
            "border-card":     "#EDE7DA",
            "border-subtle":   "#F2EDE2",
            "border-dashed":   "#D6CDB9",
            "border-check":    "#D6CFBF",

            // Ink / text
            "ink":             "#15171C",
            "text-secondary":  "#5A5C66",
            "text-muted":      "#6B6D78",
            "text-faint":      "#9A9482",
            "text-dim":        "#A9A595",
            "text-label":      "#A09A88",
            "text-olive":      "#8A8B72",
            "skeleton":        "#EDE7DA",

            // Accent gold/amber
            "gold":            "#B5751F",
            "gold-slider":     "#C0A86E",
            "gold-folder":     "#C0A86E",
            "warm-bg":         "#FBF1DF",
            "warm-border":     "#EBD9B6",
            "warm-icon":       "#F2E2C4",

            // Green (success / confident)
            "success":         "#0E8A5E",
            "success-bg":      "#E2F1E9",

            // Amber (mid confidence)
            "amber":           "#B5751F",
            "amber-bg":        "#F6E9D3",

            // Red (low / alerts)
            "danger":          "#BE4029",
            "danger-bg":       "#F7E3DD",

            // Blue (AI / reasoning)
            "ai":              "#34508C",
            "ai-bg":           "#F0F2F9",
            "ai-bg-light":     "#EEF1F9",
            "ai-border":       "#E1E6F3",
            "ai-border-light": "#DCE3F4",

            // Legacy aliases for existing code that hasn't been migrated yet
            "primary":         "#15171C",
            "on-primary":      "#F4F1EA",
            "surface":         "#F4F1EA",
            "on-surface":      "#15171C",
            "on-surface-variant": "#6B6D78",
            "secondary":       "#0E8A5E",
        },
        fontFamily: {
            headline: ["Manrope", "sans-serif"],
            body:     ["Inter", "sans-serif"],
            mono:     ["JetBrains Mono", "monospace"],
        },
        borderRadius: {
            "pill":  "999px",
            "modal": "18px",
            "card":  "16px",
            "card-sm": "13px",
            "btn":   "12px",
            "input": "10px",
            "tile":  "8px",
            "sm":    "6px",
        },
        boxShadow: {
            "card":       "0 1px 2px rgba(20,23,28,.03)",
            "raised":     "0 8px 26px rgba(20,23,28,.05)",
            "active":     "0 4px 14px rgba(20,23,28,.08)",
            "btn":        "0 6px 16px rgba(20,23,28,.16)",
            "btn-strong": "0 6px 16px rgba(20,23,28,.18)",
            "doc":        "0 10px 34px rgba(20,23,28,.14)",
            "modal":      "0 24px 60px rgba(20,23,28,.32)",
            "tab":        "0 2px 6px rgba(20,23,28,.08)",
            "snackbar":   "0 12px 34px rgba(20,23,28,.3)",
        },
        screens: {
            'sm': '640px',
            'md': '768px',
            'lg': '1024px',
            'xl': '1280px',
        },
    }}
};
