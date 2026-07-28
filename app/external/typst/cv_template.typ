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
    margin: (top: 1.8cm, bottom: 1.5cm, left: 2cm, right: 2cm),
    header-ascent: 0cm,
    numbering: none,
  )
  set text(font: "Libertinus Serif", size: 10pt)
  set par(leading: 0.65em, justify: false)

  let accent = rgb("#1a1a2e")
  let muted = rgb("#666666")

  let section-header(title) = {
    v(0.8em)
    text(weight: "bold", size: 10.5pt, fill: accent, title)
    line(length: 100%, stroke: 0.4pt + rgb("#cccccc"))
    v(0.3em)
  }

  set align(center)
  text(weight: "bold", size: 18pt, fill: accent, first + " " + last)
  v(0.15em)

  let contact-parts = ()
  if location != none { contact-parts.push(location) }
  if email != "" { contact-parts.push(email) }
  if phone != none { contact-parts.push(phone) }
  if linkedin != none { contact-parts.push(linkedin.replace("https://", "").replace("http://", "")) }
  if github != none { contact-parts.push(github.replace("https://", "").replace("http://", "")) }
  if portfolio != none { contact-parts.push(portfolio.replace("https://", "").replace("http://", "")) }

  if contact-parts.len() > 0 {
    text(fill: muted, size: 9pt, contact-parts.join("  |  "))
  }

  set align(left)

  if profile != none {
    section-header("Profile")
    text(size: 10pt, profile)
  }

  if competencies != none and competencies.len() > 0 {
    section-header("Core Competencies")
    text(size: 9.5pt, fill: accent, competencies.join("  \u2022  "))
  }

  if skills != none and skills.len() > 0 {
    section-header("Skills")
    for group in skills {
      let label = group.at("label", default: "")
      let items = group.at("skills", default: ())
      if items.len() > 0 {
        let parts = ()
        for skill in items {
          let name = skill.at("name", default: "")
          let prof = skill.at("proficiency", default: none)
          if prof != none {
            parts.push(name + " (" + prof + ")")
          } else {
            parts.push(name)
          }
        }
        text(weight: "bold", size: 9.5pt, label + ": ")
        text(size: 9.5pt, parts.join(", "))
        v(0.15em)
      }
    }
  }

  section-header("Experience")
  for entry in experience {
    let title = entry.at("title", default: "")
    let company = entry.at("company", default: "")
    let loc = entry.at("location", default: none)
    let dr = entry.at("date_range", default: (:))
    let start = dr.at("start", default: none)
    let end = dr.at("end", default: none)
    let bullets = entry.at("bullets", default: ())

    text(weight: "bold", size: 10.5pt, title)
    if company != "" {
      text(size: 10.5pt, " \u2014 " + company)
    }
    v(0.05em)

    let meta_parts = ()
    if start != none {
      let range = start
      if end != none { range = range + " \u2013 " + end }
      meta_parts.push(range)
    }
    if loc != none { meta_parts.push(loc) }
    if meta_parts.len() > 0 {
      text(fill: muted, size: 9pt, meta_parts.join("  |  "))
      v(0.15em)
    }

    for bullet in bullets {
      text(size: 10pt, "\u2022 " + bullet)
      v(0.1em)
    }
    v(0.3em)
  }

  if projects != none and projects.len() > 0 {
    section-header("Projects")
    for proj in projects {
      let pname = proj.at("name", default: "")
      let purl = proj.at("url", default: none)
      let pdesc = proj.at("description", default: none)
      text(weight: "bold", size: 10pt, pname)
      if purl != none {
        text(fill: muted, size: 9pt, " (" + purl + ")")
      }
      v(0.05em)
      if pdesc != none {
        text(size: 10pt, pdesc)
        v(0.15em)
      }
    }
  }

  if education != none and education.len() > 0 {
    section-header("Education")
    for edu in education {
      let degree = edu.at("degree", default: "")
      let institution = edu.at("institution", default: "")
      let period = edu.at("period", default: none)
      let topics = edu.at("key_topics", default: none)

      text(weight: "bold", size: 10.5pt, degree)
      text(size: 10.5pt, " \u2014 " + institution)
      v(0.05em)
      if period != none {
        text(fill: muted, size: 9pt, period)
      }
      if topics != none and topics.len() > 0 {
        v(0.05em)
        text(size: 9.5pt, "Relevant coursework: " + topics.join(", "))
      }
      v(0.3em)
    }
  }

  if certifications != none and certifications.len() > 0 {
    section-header("Certifications")
    for cert in certifications {
      let cname = cert.at("name", default: "")
      let issuer = cert.at("issuer", default: "")
      let year = cert.at("year", default: none)
      text(size: 10pt, cname)
      if issuer != "" { text(size: 10pt, " \u2014 " + issuer) }
      if year != none { text(fill: muted, size: 9pt, " (" + year + ")") }
      v(0.15em)
    }
  }

  if publications != none and publications.len() > 0 {
    section-header("Publications")
    for pub in publications {
      let authors = pub.at("authors", default: "")
      let year = pub.at("year", default: "")
      let title = pub.at("title", default: "")
      let journal = pub.at("journal", default: none)
      text(size: 10pt, authors + " (" + year + "). " + title + ".")
      if journal != none {
        text(size: 10pt, " " + journal + ".")
      }
      v(0.2em)
    }
  }

  if awards != none and awards.len() > 0 {
    section-header("Awards")
    for award in awards {
      let aname = award.at("name", default: "")
      let issuer = award.at("issuer", default: none)
      let year = award.at("year", default: none)
      text(size: 10pt, aname)
      if issuer != none { text(size: 10pt, " \u2014 " + issuer) }
      if year != none { text(fill: muted, size: 9pt, " (" + year + ")") }
      v(0.15em)
    }
  }
}
