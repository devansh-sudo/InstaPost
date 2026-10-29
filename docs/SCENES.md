# Scene reels: Shiv and Parvati talking

A 15–22 second reel. Shiv and Parvati have a short conversation in 3–4 painted pictures
(soft, traditional devotional colours), with a different Hindi voice for each and a
subtitle showing who speaks. It ends on a card with the verse and a question for the
comments.

## How it works

1. **Script.** Go to dashboard → **Scenes** → "Write" (about $0.02). Claude writes a
   conversation from a real, named episode (Shiv Purana, Shri Ramcharitmanas, Vigyan Bhairav
   Tantra …). It says which episode it follows and what it simplified. The lines are retellings,
   not invented teachings, and verse text only comes from the verified files in
   `data/scriptures/`.
2. **You read and edit.** You can change any line, the speaker, which picture shows during
   it, what each picture shows, the question, the verse and the caption. The page warns you
   if something won't work (too many words, a missing picture).
3. **Approve.** "Save and make the reel" paints the pictures and records the voices, which takes
   2–4 minutes. Gemini keeps Shiv and Parvati looking the same in every picture, because each
   later picture is painted with the first one as a reference.
4. **Final OK.** The reel lands in the inbox and on Telegram like any other post. Approve it,
   and it goes out in a reel slot to Instagram, Facebook and YouTube.

Nothing is paid for pictures or voices until you approve the script.

## Cost

Script ~$0.02, pictures ~$0.04 each, voices under $0.01: about **$0.15–0.20 per reel**.
Everything uses keys you already have (ANTHROPIC_API_KEY, GEMINI_API_KEY).

## Settings (.env)

| Setting | Default | Meaning |
|---|---|---|
| `SCENE_VOICES` | `true` | `false`: no voices, subtitles only (no AI label needed) |
| `GEMINI_TTS_MODEL` | `gemini-2.5-flash-preview-tts` | the voice model |

Voices: Shiv = "Charon" (deep, slow), Parvati = "Sulafat" (warm), narrator = "Gacrux"
(storyteller). You can change them in `src/instapost/studio/scenes.py` (CHARACTERS).

## AI label

Realistic AI voices count as AI content on Instagram. The caption always says
"चित्र और आवाज़ें AI की मदद से बनाए गए हैं" and names the source. The posting API can't switch
on Instagram's own **AI label**, so to add it, edit the reel in the app after it is posted
(… → Edit → AI label), or post scene reels yourself with `INSTAGRAM_REELS=app`.

## Command line

```
bash deploy/docker.sh cli scene sample            # make the sample scene now (~$0.15)
bash deploy/docker.sh cli scene write --topic "धैर्य"
bash deploy/docker.sh cli scene make <scene-id>
```
