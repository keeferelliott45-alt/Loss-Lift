# Ironline Gym — demo site

A single-page static website for a fictional gym. No build step, no dependencies.

## Run it

Open `index.html` in a browser, or serve the folder:

```
python3 -m http.server 8000 --directory gym
```

Then visit http://localhost:8000

## Files

- `index.html` — all page content (hero, classes, schedule, pricing, contact)
- `styles.css` — dark theme, responsive down to mobile
- `script.js` — mobile nav toggle, client-side form validation, footer year

The contact form has no backend; it validates input and shows a confirmation message.
All names, prices, and contact details are fictional.
