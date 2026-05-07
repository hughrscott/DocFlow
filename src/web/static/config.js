// DocFlow shared Tailwind configuration and theme
// Single source of truth for colors, fonts, and styles

tailwind.config = {
    darkMode: 'class',
    theme: { extend: {
        colors: {
            "surface": "#f7f9fb", "on-surface": "#191c1e", "on-surface-variant": "#45474c",
            "surface-container": "#eceef0", "surface-container-low": "#f2f4f6",
            "surface-container-lowest": "#ffffff", "surface-variant": "#e0e3e5",
            "primary": "#091426", "primary-container": "#1e293b",
            "on-primary": "#ffffff", "on-primary-container": "#8590a6",
            "secondary": "#006c49", "secondary-container": "#6cf8bb",
            "on-secondary-container": "#00714d",
            "tertiary-fixed": "#ffddb8", "tertiary-fixed-dim": "#ffb95f",
            "on-tertiary-container": "#c88000", "on-tertiary-fixed": "#2a1700",
            "on-tertiary-fixed-variant": "#653e00",
            "outline": "#75777d", "outline-variant": "#c5c6cd",
            "primary-fixed": "#d8e3fb", "primary-fixed-dim": "#bcc7de",
            "secondary-fixed": "#6ffbbe",
            "error": "#ba1a1a",
        },
        fontFamily: { headline: ["Manrope"], body: ["Inter"] },
        screens: {
            'sm': '640px',
            'md': '768px',
            'lg': '1024px',
            'xl': '1280px',
        },
    }}
};
