from __future__ import absolute_import

import argparse
import codecs
from codecs import StreamWriter  # pylint: disable=unused-import
import copy
import logging
import os
import re
import sys
from io import open, TextIOWrapper, TextIOBase
from typing import (IO, Any, Dict, List, MutableMapping, MutableSequence,
                    Optional, Set, Tuple, Union, cast)

import mistune
import six
from six import StringIO
from six.moves import range, urllib
from typing_extensions import Text  # pylint: disable=unused-import
# move to a regular typing import when Python 3.3-3.6 is no longer supported

from . import schema
from .utils import add_dictlist, aslist

_logger = logging.getLogger("salad")


def has_types(items):  # type: (Any) -> List[Text]
    r = []  # type: List[Text]
    if isinstance(items, MutableMapping):
        if items["type"] == "https://w3id.org/cwl/salad#record":
            return [items["name"]]
        for n in ("type", "items", "values"):
            if n in items:
                r.extend(has_types(items[n]))
        return r
    if isinstance(items, MutableSequence):
        for i in items:
            r.extend(has_types(i))
        return r
    if isinstance(items, six.string_types):
        return [items]
    return []


def linkto(item):  # type: (Text) -> Text
    _, frg = urllib.parse.urldefrag(item)
    return "[%s](#%s)" % (frg, to_id(frg))


class MyRenderer(mistune.Renderer):

    def __init__(self):  # type: () -> None
        super(MyRenderer, self).__init__()
        self.options = {}

    def header(self, text, level, raw=None):  # type: (Text, int, Any) -> Text
        return """<h%i id="%s" class="section">%s <a href="#%s">&sect;</a></h%i>""" % (level, to_id(text), text, to_id(text), level)

    def table(self, header, body):  # type: (Text, Text) -> Text
        return (
            '<table class="table table-striped">\n<thead>%s</thead>\n'
            '<tbody>\n%s</tbody>\n</table>\n'
        ) % (header, body)


def to_id(text):  # type: (Text) -> Text
    textid = text
    if text[0] in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
        try:
            textid = text[text.index(" ") + 1:]
        except ValueError:
            pass
    textid = textid.replace(" ", "_")
    return textid


class ToC(object):

    def __init__(self):  # type: () -> None
        self.first_toc_entry = True
        self.numbering = [0]
        self.toc = ""
        self.start_numbering = True

    def add_entry(self, thisdepth, title):  # type: (int, str) -> str
        depth = len(self.numbering)
        if thisdepth < depth:
            self.toc += "</ol>"
            for _ in range(0, depth - thisdepth):
                self.numbering.pop()
                self.toc += "</li></ol>"
            self.numbering[-1] += 1
        elif thisdepth == depth:
            if not self.first_toc_entry:
                self.toc += "</ol>"
            else:
                self.first_toc_entry = False
            self.numbering[-1] += 1
        elif thisdepth > depth:
            self.numbering.append(1)

        if self.start_numbering:
            num = "%i.%s" % (self.numbering[0], ".".join(
                [str(n) for n in self.numbering[1:]]))
        else:
            num = ""
        self.toc += """<li><a href="#%s">%s %s</a><ol>\n""" % (to_id(title),
                                                               num, title)
        return num

    def contents(self, idn):  # type: (str) -> str
        toc = """<h1 id="%s">Table of contents</h1>
               <nav class="tocnav"><ol>%s""" % (idn, self.toc)
        toc += "</ol>"
        for _ in range(0, len(self.numbering)):
            toc += "</li></ol>"
        toc += """</nav>"""
        return toc


basicTypes = ("https://w3id.org/cwl/salad#null",
              "http://www.w3.org/2001/XMLSchema#boolean",
              "http://www.w3.org/2001/XMLSchema#int",
              "http://www.w3.org/2001/XMLSchema#long",
              "http://www.w3.org/2001/XMLSchema#float",
              "http://www.w3.org/2001/XMLSchema#double",
              "http://www.w3.org/2001/XMLSchema#string",
              "https://w3id.org/cwl/salad#record",
              "https://w3id.org/cwl/salad#enum",
              "https://w3id.org/cwl/salad#array")


def number_headings(toc, maindoc):  # type: (ToC, str) -> str
    mdlines = []
    skip = False
    for line in maindoc.splitlines():
        if line.strip() == "# Introduction":
            toc.start_numbering = True
            toc.numbering = [0]

        if "```" in line:
            skip = not skip

        if not skip:
            m = re.match(r'^(#+) (.*)', line)
            if m is not None:
                num = toc.add_entry(len(m.group(1)), m.group(2))
                line = "%s %s %s" % (m.group(1), num, m.group(2))
            line = re.sub(r'^(https?://\S+)', r'[\1](\1)', line)
        mdlines.append(line)

    maindoc = '\n'.join(mdlines)
    return maindoc


def fix_doc(doc):  # type: (Union[List[str], str]) -> str
    if isinstance(doc, MutableSequence):
        docstr = "".join(doc)
    else:
        docstr = doc
    return "\n".join(
        [re.sub(r"<([^>@]+@[^>]+)>", r"[\1](mailto:\1)", d)
         for d in docstr.splitlines()])


class RenderType(object):

    def __init__(self, toc, j, renderlist, redirects, primitiveType):
        # type: (ToC, List[Dict[Text, Text]], str, Dict[Text, Text], str) -> None
        self.typedoc = StringIO()
        self.toc = toc
        self.subs = {}  # type: Dict[str, str]
        self.docParent = {}  # type: Dict[str, List[Text]]
        self.docAfter = {}  # type: Dict[str, List[Text]]
        self.rendered = set()  # type: Set[str]
        self.redirects = redirects
        self.title = None  # type: Optional[str]
        self.primitiveType = primitiveType

        for t in j:
            if "extends" in t:
                for e in aslist(t["extends"]):
                    add_dictlist(self.subs, e, t["name"])
                    # if "docParent" not in t and "docAfter" not in t:
                    #    add_dictlist(self.docParent, e, t["name"])

            if t.get("docParent"):
                add_dictlist(self.docParent, t["docParent"], t["name"])

            if t.get("docChild"):
                for c in aslist(t["docChild"]):
                    add_dictlist(self.docParent, t["name"], c)

            if t.get("docAfter"):
                add_dictlist(self.docAfter, t["docAfter"], t["name"])

        metaschema_loader = schema.get_metaschema()[2]
        alltypes = schema.extend_and_specialize(j, metaschema_loader)

        self.typemap = {}  # type: Dict[Text, Dict[Text, Text]]
        self.uses = {}  # type: Dict[Text, List[Tuple[Text, Text]]]
        self.record_refs = {}  # type: Dict[Text, List[Text]]
        for entry in alltypes:
            self.typemap[t["name"]] = t
            try:
                if t["type"] == "record":
                    self.record_refs[t["name"]] = []
                    fields = t.get("fields", [])
                    if isinstance(fields, Text):
                        raise KeyError("record fields must be a list of mappings")
                    for f in fields:  # type: Dict[Text, Text]
                        p = has_types(f)
                        for tp in p:
                            if tp not in self.uses:
                                self.uses[tp] = []
                            if (t["name"], f["name"]) not in self.uses[tp]:
                                _, frg1 = urllib.parse.urldefrag(t["name"])
                                _, frg2 = urllib.parse.urldefrag(f["name"])
                                self.uses[tp].append((frg1, frg2))
                            if tp not in basicTypes and tp not in self.record_refs[t["name"]]:
                                self.record_refs[t["name"]].append(tp)
            except KeyError:
                _logger.error("Did not find 'type' in %s", t)
                raise

        for entry in alltypes:
            if (entry["name"] in renderlist
                    or ((not renderlist) and ("extends" not in entry)
                        and ("docParent" not in entry)
                        and ("docAfter" not in entry))):
                self.render_type(entry, 1)

    def typefmt(self,
                tp,                     # type: Any
                redirects,              # type: Dict[Text, Text]
                nbsp=False,             # type: bool
                jsonldPredicate=None    # type: Optional[Dict[str, str]]
                ):
        # type: (...) -> Text
        if isinstance(tp, MutableSequence):
            if nbsp and len(tp) <= 3:
                return "&nbsp;|&nbsp;".join(
                    [self.typefmt(n, redirects, jsonldPredicate=jsonldPredicate)
                     for n in tp])
            return " | ".join(
                [self.typefmt(n, redirects, jsonldPredicate=jsonldPredicate)
                 for n in tp])
        if isinstance(tp, MutableMapping):
            if tp["type"] == "https://w3id.org/cwl/salad#array":
                ar = "array&lt;%s&gt;" % (self.typefmt(
                    tp["items"], redirects, nbsp=True))
                if jsonldPredicate is not None and "mapSubject" in jsonldPredicate:
                    if "mapPredicate" in jsonldPredicate:
                        ar += " | "
                        if len(ar) > 40:
                            ar += "<br>"

                        ar += "<a href='#map'>map</a>&lt;<code>%s</code>,&nbsp;<code>%s</code> | %s&gt" % (
                            jsonldPredicate["mapSubject"], jsonldPredicate["mapPredicate"],
                            self.typefmt(tp["items"], redirects))
                    else:
                        ar += " | "
                        if len(ar) > 40:
                            ar += "<br>"
                        ar += "<a href='#map'>map</a>&lt;<code>%s</code>,&nbsp;%s&gt" % (
                            jsonldPredicate["mapSubject"],
                            self.typefmt(tp["items"], redirects))
                return ar
            if tp["type"] in ("https://w3id.org/cwl/salad#record",
                              "https://w3id.org/cwl/salad#enum"):
                frg = schema.avro_name(tp["name"])
                if tp["name"] in redirects:
                    return """<a href="%s">%s</a>""" % (redirects[tp["name"]], frg)
                if tp["name"] in self.typemap:
                    return """<a href="#%s">%s</a>""" % (to_id(frg), frg)
                if tp["type"] == "https://w3id.org/cwl/salad#enum" and len(tp["symbols"]) == 1:
                    return "constant value <code>%s</code>" % schema.avro_name(tp["symbols"][0])
                return frg
            if isinstance(tp["type"], MutableMapping):
                return self.typefmt(tp["type"], redirects)
        else:
            if str(tp) in redirects:
                return """<a href="%s">%s</a>""" % (redirects[tp], redirects[tp])
            if str(tp) in basicTypes:
                return """<a href="%s">%s</a>""" % (self.primitiveType, schema.avro_name(str(tp)))
            _, frg = urllib.parse.urldefrag(tp)
            if frg != '':
                tp = frg
            return """<a href="#%s">%s</a>""" % (to_id(tp), tp)
        raise Exception("We should not be here!")

    def render_type(self, f, depth):  # type: (Dict[Text, Any], int) -> None
        if f["name"] in self.rendered or f["name"] in self.redirects:
            return
        self.rendered.add(f["name"])

        if f.get("abstract"):
            return

        if "doc" not in f:
            f["doc"] = ""

        f["type"] = copy.deepcopy(f)
        f["doc"] = ""
        f = f["type"]

        if "doc" not in f:
            f["doc"] = ""

        def extendsfrom(item, ex):
            # type: (Dict[Text, Any], List[Dict[Text, Any]]) -> None
            if "extends" in item:
                for e in aslist(item["extends"]):
                    ex.insert(0, self.typemap[e])
                    extendsfrom(self.typemap[e], ex)

        ex = [f]
        extendsfrom(f, ex)

        enumDesc = {}
        if f["type"] == "enum" and isinstance(f["doc"], MutableSequence):
            for e in ex:
                for i in e["doc"]:
                    idx = i.find(":")
                    if idx > -1:
                        enumDesc[i[:idx]] = i[idx + 1:]
                e["doc"] = [i for i in e["doc"] if i.find(
                    ":") == -1 or i.find(" ") < i.find(":")]

        f["doc"] = fix_doc(f["doc"])

        if f["type"] == "record":
            for field in f.get("fields", []):
                if "doc" not in field:
                    field["doc"] = ""

        if f["type"] != "documentation":
            lines = []
            for line in f["doc"].splitlines():
                if len(line) > 0 and line[0] == "#":
                    line = ("#" * depth) + line
                lines.append(line)
            f["doc"] = "\n".join(lines)

            _, frg = urllib.parse.urldefrag(f["name"])
            num = self.toc.add_entry(depth, frg)
            doc = u"%s %s %s\n" % (("#" * depth), num, frg)
        else:
            doc = u""

        if self.title is None and f["doc"]:
            title = f["doc"][0:f["doc"].index("\n")]
            if title.startswith('# '):
                self.title = title[2:]
            else:
                self.title = title

        if f["type"] == "documentation":
            f["doc"] = number_headings(self.toc, f["doc"])

        # if "extends" in f:
        #    doc += "\n\nExtends "
        #    doc += ", ".join([" %s" % linkto(ex) for ex in aslist(f["extends"])])
        # if f["name"] in self.subs:
        #    doc += "\n\nExtended by"
        #    doc += ", ".join([" %s" % linkto(s) for s in self.subs[f["name"]]])
        # if f["name"] in self.uses:
        #    doc += "\n\nReferenced by"
        #    doc += ", ".join([" [%s.%s](#%s)" % (s[0], s[1], to_id(s[0]))
        #       for s in self.uses[f["name"]]])

        doc = doc + "\n\n" + f["doc"]

        doc = mistune.markdown(doc, renderer=MyRenderer())

        if f["type"] == "record":
            doc += "<h3>Fields</h3>"
            doc += """
<div class="responsive-table">
<div class="row responsive-table-header">
<div class="col-xs-3 col-lg-2">field</div>
<div class="col-xs-2 col-lg-1">required</div>
<div class="col-xs-7 col-lg-3">type</div>
<div class="col-xs-12 col-lg-6 description-header">description</div>
</div>"""
            required = []
            optional = []
            for i in f.get("fields", []):
                tp = i["type"]
                if isinstance(tp, MutableSequence) and tp[0] == "https://w3id.org/cwl/salad#null":
                    opt = False
                    tp = tp[1:]
                else:
                    opt = True

                desc = i["doc"]
                # if "inherited_from" in i:
                #    desc = "%s _Inherited from %s_" % (desc, linkto(i["inherited_from"]))

                rfrg = schema.avro_name(i["name"])
                tr = """
<div class="row responsive-table-row">
<div class="col-xs-3 col-lg-2"><code>%s</code></div>
<div class="col-xs-2 col-lg-1">%s</div>
<div class="col-xs-7 col-lg-3">%s</div>
<div class="col-xs-12 col-lg-6 description-col">%s</div>
</div>""" % (rfrg, "required" if opt else "optional",
                    self.typefmt(tp, self.redirects,
                    jsonldPredicate=i.get("jsonldPredicate")),
                    mistune.markdown(desc))

                if opt:
                    required.append(tr)
                else:
                    optional.append(tr)
            for i in required + optional:
                doc += i
            doc += """</div>"""
        elif f["type"] == "enum":
            doc += "<h3>Symbols</h3>"
            doc += """<table class="table table-striped">"""
            doc += "<tr><th>symbol</th><th>description</th></tr>"
            for e in ex:
                for i in e.get("symbols", []):
                    doc += "<tr>"
                    efrg = schema.avro_name(i)
                    doc += "<td><code>%s</code></td><td>%s</td>" % (
                        efrg, enumDesc.get(efrg, ""))
                    doc += "</tr>"
            doc += """</table>"""
        f["doc"] = doc

        self.typedoc.write(f["doc"])

        subs = self.docParent.get(f["name"], []) + \
            self.record_refs.get(f["name"], [])
        if len(subs) == 1:
            self.render_type(self.typemap[subs[0]], depth)
        else:
            for s in subs:
                self.render_type(self.typemap[s], depth + 1)

        for s in self.docAfter.get(f["name"], []):
            self.render_type(self.typemap[s], depth)


def avrold_doc(j,           # type: List[Dict[Text, Any]]
               outdoc,      # type: Union[IO[Any], StreamWriter]
               renderlist,  # type: str
               redirects,   # type: Dict[Text, Text]
               brand,       # type: str
               brandlink,   # type: str
               primtype     # type: str
              ):  # type: (...) -> None
    toc = ToC()
    toc.start_numbering = False

    rt = RenderType(toc, j, renderlist, redirects, primtype)
    content = rt.typedoc.getvalue()  # type: Text

    outdoc.write("""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.4/css/bootstrap.min.css" integrity="sha384-604wwakM23pEysLJAhja8Lm42IIwYrJ0dEAqzFsj9pJ/P5buiujjywArgPCi8eoz" crossorigin="anonymous">
    <script>
    // Picture element HTML5 shiv
    document.createElement( "picture" );
    </script>
    <script src="https://cdn.rawgit.com/scottjehl/picturefill/3.0.2/dist/picturefill.min.js" integrity="sha384-ZJsVW8YHHxQHJ+SJDncpN90d0EfAhPP+yA94n+EhSRzhcxfo84yMnNk+v37RGlWR" crossorigin="anonymous" async></script>
    """)

    outdoc.write("<title>%s</title>" % (rt.title))

    outdoc.write("""
    <style>
    :target {
      padding-top: 61px;
      margin-top: -61px;
    }
    body {
      padding-top: 61px;
    }
    .tocnav ol {
      list-style: none
    }
    pre {
      margin-left: 2em;
      margin-right: 2em;
    }
    .section a {
      visibility: hidden;
    }
    .section:hover a {
      visibility: visible;
      color: rgb(201, 201, 201);
    }
    .responsive-table-header {
      text-align: left;
      padding: 8px;
      vertical-align: top;
      font-weight: bold;
      border-top-color: rgb(221, 221, 221);
      border-top-style: solid;
      border-top-width: 1px;
      background-color: #f9f9f9
    }
    .responsive-table > .responsive-table-row {
      text-align: left;
      padding: 8px;
      vertical-align: top;
      border-top-color: rgb(221, 221, 221);
      border-top-style: solid;
      border-top-width: 1px;
    }
    @media (min-width: 0px), print {
      .description-header {
        display: none;
      }
      .description-col {
        margin-top: 1em;
        margin-left: 1.5em;
      }
    }
    @media (min-width: 1170px) {
      .description-header {
        display: inline;
      }
      .description-col {
        margin-top: 0px;
        margin-left: 0px;
      }
    }
    .responsive-table-row:nth-of-type(odd) {
       background-color: #f9f9f9
    }
    </style>
    </head>
    <body>
    """)

    outdoc.write("""
      <nav class="navbar navbar-default navbar-fixed-top">
        <div class="container">
          <div class="navbar-header">
            <a class="navbar-brand" href="%s">%s</a>
    """ % (brandlink, brand))

    if u"<!--ToC-->" in content:
        content = content.replace(u"<!--ToC-->", toc.contents("toc"))
        outdoc.write("""
                <ul class="nav navbar-nav">
                  <li><a href="#toc">Table of contents</a></li>
                </ul>
        """)

    outdoc.write("""
          </div>
        </div>
      </nav>
    """)

    outdoc.write("""
    <div class="container">
    """)

    outdoc.write("""
    <div class="row">
    """)

    outdoc.write("""
    <div class="col-md-12" role="main" id="main">""")

    outdoc.write(content)

    outdoc.write("""</div>""")

    outdoc.write("""
    </div>
    </div>
    </body>
    </html>""")


def main():  # type: () -> None
    parser = argparse.ArgumentParser()
    parser.add_argument("schema")
    parser.add_argument('--only', action='append')
    parser.add_argument('--redirect', action='append')
    parser.add_argument('--brand')
    parser.add_argument('--brandlink')
    parser.add_argument('--primtype', default="#PrimitiveType")

    args = parser.parse_args()

    makedoc(args)

def makedoc(args):   # type: (argparse.Namespace) -> None

    s = []  # type: List[Dict[Text, Any]]
    a = args.schema
    with open(a, encoding='utf-8') as f:
        if a.endswith("md"):
            s.append({"name": os.path.splitext(os.path.basename(a))[0],
                      "type": "documentation",
                      "doc": f.read()
                      })
        else:
            uri = "file://" + os.path.abspath(a)
            metaschema_loader = schema.get_metaschema()[2]
            j, _ = metaschema_loader.resolve_ref(uri, "")
            if isinstance(j, MutableSequence):
                s.extend(j)
            elif isinstance(j, MutableMapping):
                s.append(j)
            else:
                raise ValueError("Schema must resolve to a list or a dict")
    redirect = {}
    for r in (args.redirect or []):
        redirect[r.split("=")[0]] = r.split("=")[1]
    renderlist = args.only if args.only else []
    stdout = cast(TextIOWrapper, sys.stdout)  # type: Union[TextIOWrapper, StreamWriter]
    if sys.stdout.encoding != 'UTF-8':
        if sys.version_info >= (3,):
            stdout = TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        else:
            stdout = codecs.getwriter('utf-8')(sys.stdout)
    avrold_doc(s, stdout, renderlist, redirect, args.brand, args.brandlink, args.primtype)


if __name__ == "__main__":
    main()
