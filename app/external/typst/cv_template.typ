#let _months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#let _fmt_date(iso) = {
  if iso == none or iso == "" { return none }
  if iso == "Present" { return "Present" }
  let parts = iso.split("-")
  if parts.len() == 1 { return parts.at(0) }
  let m = int(parts.at(1))
  let mn = if m >= 1 and m <= 12 { _months.at(m - 1) } else { parts.at(1) }
  return mn + " " + parts.at(0)
}

#let fmt_date_range(dr) = {
  let start = dr.at("start", default: none)
  let end = dr.at("end", default: none)
  let sf = _fmt_date(start)
  let ef = _fmt_date(end)
  if sf == none and ef == none { return "" }
  if sf == none { return ef }
  if ef == none or ef == "" or ef == "Present" { return sf + " – Present" }
  if ef == sf { return sf }
  return sf + " – " + ef
}

#let _contact_strip(url) = {
  url.replace("https://", "").replace("http://", "").replace("www.", "")
}

#let _section-header(title) = {
  v(0.4em)
  text(weight: "bold", size: 10pt, title)
  line(length: 100%, stroke: 0.3pt + black)
  v(0.15em)
}

#let _bold(it) = text(weight: "bold", it)
#let _italic(it) = text(style: "italic", it)

#let render_cv(data) = {
  let first = data.at("first_name", default: "")
  let last = data.at("last_name", default: "")
  let email = data.at("email", default: "")
  let phone = data.at("phone", default: none)
  let location = data.at("location", default: none)
  let linkedin = data.at("linkedin", default: none)
  let github = data.at("github", default: none)
  let portfolio = data.at("portfolio_url", default: none)
  let profile = data.at("profile_statement", default: none)
  let competencies = data.at("core_competencies", default: none)
  let skills = data.at("skills", default: none)
  let experience = data.at("experience", default: ())
  let projects = data.at("projects", default: none)
  let education = data.at("education", default: none)
  let certifications = data.at("certifications", default: none)
  let publications = data.at("publications", default: none)
  let awards = data.at("awards", default: none)

  set page(
    paper: "a4",
    margin: (top: 0.5in, bottom: 0.45in, left: 0.6in, right: 0.6in),
    header-ascent: 0cm,
    numbering: none,
  )
  set text(font: "Latin Modern Roman", size: 9.5pt)
  set par(leading: 0.4em, justify: false)

  let muted = rgb("#444444")

  // ── Header ──────────────────────────────────────────────────────
  set align(center)
  text(weight: "bold", size: 15pt, first + " " + last)
  v(0.08em)

  let contact-parts = ()
  if location != none { contact-parts.push(location) }
  if email != "" { contact-parts.push(email) }
  if phone != none { contact-parts.push(phone) }
  if linkedin != none { contact-parts.push(_contact_strip(linkedin)) }
  if github != none { contact-parts.push(_contact_strip(github)) }
  if portfolio != none { contact-parts.push(_contact_strip(portfolio)) }

  if contact-parts.len() > 0 {
    text(fill: muted, size: 9pt, contact-parts.join(" | "))
  }

  set align(left)

  // ── Profile (compact) ─────────────────────────────────────────
  if profile != none {
    v(0.2em)
    text(size: 9.5pt, fill: muted, profile)
  }

  // ── Experience (first) ──────────────────────────────────────
  _section-header("Experience")
  for entry in experience {
    let title = entry.at("title", default: "")
    let company = entry.at("company", default: "")
    let loc = entry.at("location", default: none)
    let dr = entry.at("date_range", default: (:))
    let bullets = entry.at("bullets", default: ())

    // Line 1: Company (bold) + Location (right, inline)
    if company != "" {
      _bold(company)
      if loc != none {
        h(1fr)
        text(fill: muted, size: 9pt, loc)
      }
      linebreak()
    }

    // Line 2: Role (italic) + Dates (right, inline)
    let dstr = fmt_date_range(dr)
    if title != "" {
      _italic(title)
      if dstr != "" {
        h(1fr)
        text(fill: muted, size: 9pt, dstr)
      }
      linebreak()
    } else if dstr != "" {
      text(fill: muted, size: 9pt, dstr)
      linebreak()
    }

    v(0.04em)

    for bullet in bullets {
      text(size: 9pt, "\u2022 " + bullet)
      v(0.06em)
    }
    v(0.2em)
  }

  // ── Core Competencies ───────────────────────────────────────
  if competencies != none and competencies.len() > 0 {
    _section-header("Core Competencies")
    text(size: 9.5pt, fill: muted, competencies.join("  \u2022  "))
  }

  // ── Skills ──────────────────────────────────────────────────
  if skills != none and skills.len() > 0 {
    _section-header("Skills")
    for group in skills {
      let label = group.at("label", default: "")
      let items = group.at("skills", default: ())
      if items.len() > 0 {
        let parts = ()
        for skill in items {
          parts.push(skill.at("name", default: ""))
        }
        _bold(label + ": ") + text(parts.join(", "))
        linebreak()
      }
    }
  }

  // ── Projects ────────────────────────────────────────────────
  if projects != none and projects.len() > 0 {
    _section-header("Projects")
    for proj in projects {
      let pname = proj.at("name", default: "")
      let purl = proj.at("url", default: none)
      let pdesc = proj.at("description", default: none)
      let techs = proj.at("technologies", default: none)
      _bold(pname)
      if purl != none {
        text(fill: muted, size: 9pt, " (" + _contact_strip(purl) + ")")
      }
      linebreak()
      if pdesc != none {
        text(size: 9.5pt, pdesc)
        linebreak()
      }
      if techs != none and techs.len() > 0 {
        text(size: 9pt, fill: muted, "Tech: " + techs.join(", "))
        linebreak()
      }
      v(0.12em)
    }
  }

  // ── Education ───────────────────────────────────────────────
  if education != none and education.len() > 0 {
    _section-header("Education")
    for edu in education {
      let degree = edu.at("degree", default: "")
      let institution = edu.at("institution", default: "")
      let period = edu.at("period", default: none)
      let dr = edu.at("date_range", default: (:))
      let topics = edu.at("key_topics", default: none)
      let dstr = if period != none { period } else { fmt_date_range(dr) }

      _bold(degree + ", " + institution)
      if dstr != "" {
        h(1fr)
        text(fill: muted, size: 9.5pt, dstr)
      }
      linebreak()
      if topics != none and topics.len() > 0 {
        text(size: 9pt, fill: muted, "Relevant coursework: " + topics.join(", "))
        linebreak()
      }
      v(0.15em)
    }
  }

  // ── Certifications ─────────────────────────────────────────
  if certifications != none and certifications.len() > 0 {
    _section-header("Certifications")
    for cert in certifications {
      let cname = cert.at("name", default: "")
      let issuer = cert.at("issuer", default: "")
      let year = cert.at("year", default: none)
      _bold(cname)
      if issuer != "" { text(", " + issuer) }
      if year != none {
        h(1fr)
        text(fill: muted, size: 9.5pt, year)
      }
      linebreak()
      v(0.12em)
    }
  }

  // ── Publications ────────────────────────────────────────────
  if publications != none and publications.len() > 0 {
    _section-header("Publications")
    for pub in publications {
      let authors = pub.at("authors", default: "")
      let year = pub.at("year", default: "")
      let ptitle = pub.at("title", default: "")
      let journal = pub.at("journal", default: none)
      text(size: 9.5pt, authors + " (" + year + "). " + ptitle + ".")
      if journal != none {
        text(size: 9.5pt, fill: muted, " " + journal + ".")
      }
      linebreak()
      v(0.12em)
    }
  }

  // ── Awards ──────────────────────────────────────────────────
  if awards != none and awards.len() > 0 {
    _section-header("Awards")
    for award in awards {
      let aname = award.at("name", default: "")
      let issuer = award.at("issuer", default: none)
      let year = award.at("year", default: none)
      _bold(aname)
      if issuer != none { text(", " + issuer) }
      if year != none {
        h(1fr)
        text(fill: muted, size: 9.5pt, year)
      }
      linebreak()
      v(0.15em)
    }
  }
}
