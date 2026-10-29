# Ink drawings (Drawings page)

Every reel and carousel page has one ink drawing, all in the same engraving style (black pen
lines, cross-hatching, white paper), so the whole grid looks like one book.

## Which drawing a post gets
1. **A library drawing whose tags match the post's subject** (least recently used first).
   The writer is told which drawings are on hand and prefers those subjects when they fit.
2. **A new Gemini drawing** in the fixed style, when no drawing matches and the Gemini key has
   billing (about $0.04). It is saved to the library, so the next post on that subject is free.
   Turn this off with `DRAWINGS_GENERATE=false`.
3. **Any drawing that suits the pillar.**
4. The old way (an Unsplash photo turned into a sketch), only if the library is empty.

Every drawing is cleaned before use: paper to pure white, lines to black, trimmed. Photos are
refused. `drawing.txt` in each post's folder says which drawing it used.

## The library
- **Starter set** (`data/drawings/starter/`, drawn in code): diya, lotus, trishul and damru,
  bansuri and peacock feather, Kailash with the moon, Om, sunrise, chariot wheel, matka, mala.
  Symbols only; figures (Shiva, Krishna, Kabir) come from the next two sources.
- **Your uploads**: dashboard → Drawings → Upload, with tags (what is in the drawing).
- **Made by Gemini**: saved automatically, or all at once: Drawings → Make the pack (or
  `bash deploy/docker.sh cli drawings pack --yes`), 32 subjects for about $1.25 once.

## Making drawings for free
Open the Gemini or ChatGPT app, write a subject from the prompt pack (Drawings page), add the
style text shown there, save the picture and upload it with its tags. For example:

> Lord Shiva meditating seen from behind, sitting on a rock, trishul beside him, crescent moon
> in his hair, Himalaya peaks and a still lake ahead. Black ink illustration in the style of a
> vintage book engraving: fine pen lines and cross-hatching for shading, no solid fills, no
> colour, no grey wash. Pure white background, the subject centred with a wide empty margin,
> no frame, no border. Respectful, traditional Indian depiction. No text or letters.

Check each picture before uploading: correct attributes (trishul, crescent moon, flute),
respectful pose, no letters or watermarks.
