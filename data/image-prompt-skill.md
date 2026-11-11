---
name: image-prompt-skill
description: Expand an image-generation request into a detailed Image caption prompt. Use for prompt enhancement and image description; this skill does not generate images.
---

# image-prompt-skill

Turn the user's idea into a concrete image description. The agent makes all
creative decisions and writes the final caption directly. No external service,
model API or script is needed.

## Develop the image

Describe visible choices: subject and action, relevant appearance, setting,
camera/framing, medium, lighting, materials, palette and mood. Add detail where
it makes the requested image more specific. Avoid redundant quality tags,
unrequested style changes, arbitrary word budgets and invisible process notes.
Keep an explicit minimalist brief minimalist; develop a rich brief without
turning it into a generic title/hero/footer template.
Keep explanations of formatting or process out of the image description; those
are instructions, not depicted content.

For designed images, compose the information and imagery together. Establish
reading order, relative prominence, coherent groups and readable text at the
chosen pixels. Write the intended copy rather than placeholders such as
"headline here" or "some explanatory text". Do not import unrelated names,
watermarks or copy from a reference. Use supplied references when useful; outside
research is optional unless needed for factual accuracy or requested by the user.

Preserve the user's subjects, counts, relationships, medium, style, exclusions
and intended visible wording. Resolve ordinary creative gaps yourself; ask only
when a missing decision would materially change the requested image. Do not
invent factual event logistics or specifications as if the user supplied them.

Default to English for descriptive prose, following a requested output language
when given. Preserve visible text verbatim in its original language, including
punctuation, capitalization and requested line breaks. A quoted sentence in an
instruction is not necessarily text to render: distinguish visible wording from
reference material and constraints.

## Caption

Write the caption as cohesive prose, with spatial relations in words and any
visible wording quoted exactly. It may contain paragraphs. Do not add
coordinates, bounding boxes or JSON-layout fields to a caption.

## Deliver

Return the ready-to-paste caption prompt directly as plain text. Only the caption
belongs in Image's positive prompt. Producing this prompt does not submit
image generation or publish anything.