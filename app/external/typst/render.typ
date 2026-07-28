#import "cv_template.typ": render_cv
#import "cover_template.typ": render_cover_letter

#let render(json_path) = {
  let raw_data = json(json_path)
  let cv_data = raw_data.at("cv", default: raw_data)
  render_cv(cv_data)
  let cl = cv_data.at("cover_letter", default: none)
  if cl != none {
    pagebreak()
    render_cover_letter(cv_data)
  }
}
