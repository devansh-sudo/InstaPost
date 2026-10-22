# Your own pictures (free image source)

Used when Gemini can't make images (no billing, quota) or when `IMAGE_PROVIDER=library`.

| Folder | What to put there | Used as |
|---|---|---|
| `scenes/` | Portrait photos: diya/candle light, temple at dusk, window light, mountains, river ghat, books on a table | Blurred background behind the book |
| `subjects/` | What the drawing on the page shows: Shiva statue, Krishna with flute, Kabir, lotus, banyan tree, a person meditating | Turned into a pencil sketch automatically (real sketches are used as they are) |

**Name files by what they show**, words separated by `-`, for example
`shiva-meditating-himalaya.jpg`, `krishna-flute-tree.jpg`, `diya-temple-evening.jpg`.
The agent matches these words to each post (Shiv/Mahadev/Shankar all count as `shiva`,
Kanha as `krishna`, lamp/candle as `diya`) and uses the least recently used picture first.
Aim for 10+ scenes and 5+ subjects per pillar so posts don't repeat.

Where to get them: [unsplash.com](https://unsplash.com) (free for commercial use, no
attribution required), [pexels.com](https://pexels.com), or your own photos. Avoid
pictures with text, logos or watermarks, and photos of identifiable people.

These files stay on your machine: git ignores everything in this folder except this README.
