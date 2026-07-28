#let render_cover_letter(data) = {
  let first = data.at("first_name", default: "")
  let last = data.at("last_name", default: "")
  let email = data.at("email", default: "")
  let phone = data.at("phone", default: none)
  let location = data.at("location", default: none)
  let cl = data.at("cover_letter", default: none)

  set page(
    paper: "a4",
    margin: (top: 2.3cm, bottom: 2.3cm, left: 2.3cm, right: 2.3cm),
    numbering: none,
  )
  set text(font: "Libertinus Serif", size: 10.5pt)
  set par(leading: 0.6em, justify: false)

  let accent = rgb("#1a1a2e")
  let muted = rgb("#666666")

  set align(center)
  text(weight: "bold", size: 14pt, fill: accent, first + " " + last)
  v(0.1em)

  let contact_parts = ()
  if location != none { contact_parts.push(location) }
  if email != "" { contact_parts.push(email) }
  if phone != none { contact_parts.push(phone) }
  if contact_parts.len() > 0 {
    text(fill: muted, size: 9pt, contact_parts.join("  |  "))
  }

  line(length: 100%, stroke: 0.3pt + rgb("#cccccc"))
  v(0.6em)
  set align(left)

  if cl == none {
    text("No cover letter provided.")
    return
  }

  let opening = cl.at("opening_paragraph", default: "")
  let body = cl.at("body_paragraphs", default: ())
  let company = cl.at("company_connection_paragraph", default: none)
  let closing = cl.at("closing_paragraph", default: "")

  if opening != "" {
    text(opening)
    v(0.4em)
  }

  for para in body {
    text(para)
    v(0.4em)
  }

  if company != none and company != "" {
    text(company)
    v(0.4em)
  }

  if closing != "" {
    v(0.2em)
    text(closing)
  }

  v(1em)
  text("Sincerely,")
  v(0.8em)
  text(weight: "bold", first + " " + last)
}
