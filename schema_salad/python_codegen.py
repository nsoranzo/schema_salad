"""Python code generator for a given schema salad definition."""
from io import StringIO
from typing import IO, Any, Dict, List, MutableMapping, MutableSequence, Optional, Union

from pkg_resources import resource_stream

from . import schema
from .codegen_base import CodeGenBase, TypeDef
from .exceptions import SchemaException
from .schema import shortname

prims = {
    "http://www.w3.org/2001/XMLSchema#string": TypeDef(
        "strtype", "_PrimitiveLoader((str, str))"
    ),
    "http://www.w3.org/2001/XMLSchema#int": TypeDef("inttype", "_PrimitiveLoader(int)"),
    "http://www.w3.org/2001/XMLSchema#long": TypeDef(
        "inttype", "_PrimitiveLoader(int)"
    ),
    "http://www.w3.org/2001/XMLSchema#float": TypeDef(
        "floattype", "_PrimitiveLoader(float)"
    ),
    "http://www.w3.org/2001/XMLSchema#double": TypeDef(
        "floattype", "_PrimitiveLoader(float)"
    ),
    "http://www.w3.org/2001/XMLSchema#boolean": TypeDef(
        "booltype", "_PrimitiveLoader(bool)"
    ),
    "https://w3id.org/cwl/salad#null": TypeDef(
        "None_type", "_PrimitiveLoader(type(None))"
    ),
    "https://w3id.org/cwl/salad#Any": TypeDef("Any_type", "_AnyLoader()"),
}


class PythonCodeGen(CodeGenBase):
    """Generation of Python code for a given Schema Salad definition."""

    def __init__(self, out):
        # type: (IO[str]) -> None
        super(PythonCodeGen, self).__init__()
        self.out = out
        self.current_class_is_abstract = False
        self.serializer = StringIO()
        self.idfield = ""

    @staticmethod
    def safe_name(name):  # type: (str) -> str
        avn = schema.avro_name(name)
        if avn in ("class", "in"):
            # reserved words
            avn = avn + "_"
        return avn

    def prologue(self):
        # type: () -> None

        self.out.write(
            """#
# This file was autogenerated using schema-salad-tool --codegen=python
# The code itself is released under the Apache 2.0 license and the help text is
# subject to the license of the original schema.
#
"""
        )

        stream = resource_stream(__name__, "python_codegen_support.py")
        self.out.write(stream.read().decode("UTF-8"))
        stream.close()
        self.out.write("\n\n")

        for primative in prims.values():
            self.declare_type(primative)

    def begin_class(
        self,  # pylint: disable=too-many-arguments
        classname,  # type: str
        extends,  # type: MutableSequence[str]
        doc,  # type: str
        abstract,  # type: bool
        field_names,  # type: MutableSequence[str]
        idfield,  # type: str
        optional_fields, # type: MutableSequence[str]
    ):  # type: (...) -> None
        classname = self.safe_name(classname)

        if extends:
            ext = ", ".join(self.safe_name(e) for e in extends)
        else:
            ext = "Savable"

        self.out.write("class {}({}):\n".format(classname, ext))

        if doc:
            self.out.write(u'    """\n')
            self.out.write(str(doc))
            self.out.write(u'\n    """\n')

        self.serializer = StringIO()

        self.current_class_is_abstract = abstract
        if self.current_class_is_abstract:
            self.out.write("    pass\n\n\n")
            return

        required_field_names = [f for f in field_names if f not in optional_fields]
        optional_field_names = [f for f in field_names if f in optional_fields]

        safe_inits = ["        self,"]  # type: List[str]
        safe_inits.extend(
            [
                "        {},  # type: Any".format(self.safe_name(f))
                for f in required_field_names
                if f != "class"
            ]
        )
        safe_inits.extend(
            [
                "        {}=None,  # type: Any".format(self.safe_name(f))
                for f in optional_field_names
                if f != "class"
            ]
        )
        self.out.write(
            "    def __init__(\n"
            + "\n".join(safe_inits)
            + "\n        extension_fields=None,  "
            + "# type: Optional[Dict[str, Any]]"
            + "\n        loadingOptions=None  # type: Optional[LoadingOptions]"
            + "\n    ):  # type: (...) -> None\n"
            + """
        if extension_fields:
            self.extension_fields = extension_fields
        else:
            self.extension_fields = yaml.comments.CommentedMap()
        if loadingOptions:
            self.loadingOptions = loadingOptions
        else:
            self.loadingOptions = LoadingOptions()
"""
        )
        field_inits = ""
        for name in field_names:
            if name == "class":
                field_inits += """        self.class_ = "{}"
""".format(
                    classname
                )
            else:
                field_inits += """        self.{0} = {0}
""".format(
                    self.safe_name(name)
                )
        self.out.write(
            field_inits
            + """
    @classmethod
    def fromDoc(cls, doc, baseuri, loadingOptions, docRoot=None):
        # type: (Any, str, LoadingOptions, Optional[str]) -> {}

        _doc = copy.copy(doc)
        if hasattr(doc, 'lc'):
            _doc.lc.data = doc.lc.data
            _doc.lc.filename = doc.lc.filename
        _errors__ = []
""".format(
                classname
            )
        )

        self.idfield = idfield

        self.serializer.write(
            """
    def save(self, top=False, base_url="", relative_uris=True):
        # type: (bool, str, bool) -> Dict[str, Any]
        r = yaml.comments.CommentedMap()  # type: Dict[str, Any]
        for ef in self.extension_fields:
            r[prefix_url(ef, self.loadingOptions.vocab)] = self.extension_fields[ef]
"""
        )

        if "class" in field_names:
            self.out.write(
                """
        if _doc.get('class') != '{class_}':
            raise ValidationException("Not a {class_}")

""".format(
                    class_=classname
                )
            )

            self.serializer.write(
                """
        r['class'] = '{class_}'
""".format(
                    class_=classname
                )
            )

    def end_class(self, classname, field_names):
        # type: (str, List[str]) -> None

        if self.current_class_is_abstract:
            return

        self.out.write(
            """
        extension_fields = yaml.comments.CommentedMap()
        for k in _doc.keys():
            if k not in cls.attrs:
                if ":" in k:
                    ex = expand_url(k,
                                    "",
                                    loadingOptions,
                                    scoped_id=False,
                                    vocab_term=False)
                    extension_fields[ex] = _doc[k]
                else:
                    _errors__.append(
                        ValidationException(
                            "invalid field `%s`, expected one of: {attrstr}" % (k),
                            SourceLine(_doc, k, str)
                        )
                    )
                    break

        if _errors__:
            raise ValidationException(\"Trying '{class_}'\", None, _errors__)
""".format(
                attrstr=", ".join(["`{}`".format(f) for f in field_names]),
                class_=self.safe_name(classname),
            )
        )

        self.serializer.write(
            """
        if top and self.loadingOptions.namespaces:
            r["$namespaces"] = self.loadingOptions.namespaces

"""
        )

        self.serializer.write("        return r\n\n")

        self.serializer.write(
            "    attrs = frozenset({attrs})\n".format(attrs=field_names)
        )

        safe_inits = [
            self.safe_name(f) for f in field_names if f != "class"
        ]  # type: List[str]

        safe_inits.extend(
            ["extension_fields=extension_fields", "loadingOptions=loadingOptions"]
        )

        self.out.write(
            """        loadingOptions = copy.deepcopy(loadingOptions)
        loadingOptions.original_doc = _doc
"""
        )
        self.out.write("        return cls(" + ", ".join(safe_inits) + ")\n")

        self.out.write(str(self.serializer.getvalue()))

        self.out.write("\n\n")

    def type_loader(self, type_declaration):
        # type: (Union[List[Any], Dict[str, Any], str]) -> TypeDef

        if isinstance(type_declaration, MutableSequence):
            sub = [self.type_loader(i) for i in type_declaration]
            return self.declare_type(
                TypeDef(
                    "union_of_{}".format("_or_".join(s.name for s in sub)),
                    "_UnionLoader(({},))".format(", ".join(s.name for s in sub)),
                )
            )
        if isinstance(type_declaration, MutableMapping):
            if type_declaration["type"] in (
                "array",
                "https://w3id.org/cwl/salad#array",
            ):
                i = self.type_loader(type_declaration["items"])
                return self.declare_type(
                    TypeDef(
                        "array_of_{}".format(i.name), "_ArrayLoader({})".format(i.name)
                    )
                )
            if type_declaration["type"] in ("enum", "https://w3id.org/cwl/salad#enum"):
                for sym in type_declaration["symbols"]:
                    self.add_vocab(shortname(sym), sym)
                return self.declare_type(
                    TypeDef(
                        self.safe_name(type_declaration["name"]) + "Loader",
                        '_EnumLoader(("{}",))'.format(
                            '", "'.join(
                                self.safe_name(sym)
                                for sym in type_declaration["symbols"]
                            )
                        ),
                    )
                )
            if type_declaration["type"] in (
                "record",
                "https://w3id.org/cwl/salad#record",
            ):
                return self.declare_type(
                    TypeDef(
                        self.safe_name(type_declaration["name"]) + "Loader",
                        "_RecordLoader({})".format(
                            self.safe_name(type_declaration["name"])
                        ),
                    )
                )
            raise SchemaException("wft {}".format(type_declaration["type"]))
        if type_declaration in prims:
            return prims[type_declaration]
        return self.collected_types[self.safe_name(type_declaration) + "Loader"]

    def declare_id_field(self, name, fieldtype, doc, optional):
        # type: (str, TypeDef, str, bool) -> None

        if self.current_class_is_abstract:
            return

        self.declare_field(name, fieldtype, doc, True)

        if optional:
            opt = """{safename} = "_:" + str(_uuid__.uuid4())""".format(
                safename=self.safe_name(name)
            )
        else:
            opt = """raise ValidationException("Missing {fieldname}")""".format(
                fieldname=shortname(name)
            )

        self.out.write(
            """
        if {safename} is None:
            if docRoot is not None:
                {safename} = docRoot
            else:
                {opt}
        baseuri = {safename}
""".format(
                safename=self.safe_name(name), opt=opt
            )
        )

    def declare_field(
        self, name: str, fieldtype: TypeDef, doc: Optional[str], optional: bool
    ) -> None:

        if self.current_class_is_abstract:
            return

        if shortname(name) == "class":
            return

        if optional:
            self.out.write(
                "        if '{fieldname}' in _doc:\n".format(fieldname=shortname(name))
            )
            spc = "    "
        else:
            spc = ""
        self.out.write(
            """{spc}        try:
{spc}            {safename} = load_field(_doc.get(
{spc}                '{fieldname}'), {fieldtype}, baseuri, loadingOptions)
{spc}        except ValidationException as e:
{spc}            _errors__.append(
{spc}                ValidationException(
{spc}                    \"the `{fieldname}` field is not valid because:\",
{spc}                    SourceLine(_doc, '{fieldname}', str),
{spc}                    [e]
{spc}                )
{spc}            )
""".format(
                safename=self.safe_name(name),
                fieldname=shortname(name),
                fieldtype=fieldtype.name,
                spc=spc,
            )
        )
        if optional:
            self.out.write(
                """        else:
            {safename} = None
""".format(
                    safename=self.safe_name(name)
                )
            )

        if name == self.idfield or not self.idfield:
            baseurl = "base_url"
        else:
            baseurl = "self.{}".format(self.safe_name(self.idfield))

        if fieldtype.is_uri:
            self.serializer.write(
                """
        if self.{safename} is not None:
            u = save_relative_uri(
                self.{safename},
                {baseurl},
                {scoped_id},
                {ref_scope},
                relative_uris)
            if u:
                r['{fieldname}'] = u
""".format(
                    safename=self.safe_name(name),
                    fieldname=shortname(name).strip(),
                    baseurl=baseurl,
                    scoped_id=fieldtype.scoped_id,
                    ref_scope=fieldtype.ref_scope,
                )
            )
        else:
            self.serializer.write(
                """
        if self.{safename} is not None:
            r['{fieldname}'] = save(
                self.{safename},
                top=False,
                base_url={baseurl},
                relative_uris=relative_uris)
""".format(
                    safename=self.safe_name(name),
                    fieldname=shortname(name),
                    baseurl=baseurl,
                )
            )

    def uri_loader(self, inner, scoped_id, vocab_term, ref_scope):
        # type: (TypeDef, bool, bool, Union[int, None]) -> TypeDef
        return self.declare_type(
            TypeDef(
                "uri_{}_{}_{}_{}".format(inner.name, scoped_id, vocab_term, ref_scope),
                "_URILoader({}, {}, {}, {})".format(
                    inner.name, scoped_id, vocab_term, ref_scope
                ),
                is_uri=True,
                scoped_id=scoped_id,
                ref_scope=ref_scope,
            )
        )

    def idmap_loader(self, field, inner, map_subject, map_predicate):
        # type: (str, TypeDef, str, Union[str, None]) -> TypeDef
        return self.declare_type(
            TypeDef(
                "idmap_{}_{}".format(self.safe_name(field), inner.name),
                "_IdMapLoader({}, '{}', '{}')".format(
                    inner.name, map_subject, map_predicate
                ),
            )
        )

    def typedsl_loader(self, inner, ref_scope):
        # type: (TypeDef, Union[int, None]) -> TypeDef
        return self.declare_type(
            TypeDef(
                "typedsl_{}_{}".format(inner.name, ref_scope),
                "_TypeDSLLoader({}, {})".format(inner.name, ref_scope),
            )
        )

    def epilogue(self, root_loader):
        # type: (TypeDef) -> None
        self.out.write("_vocab = {\n")
        for k in sorted(self.vocab.keys()):
            self.out.write(u'    "{}": "{}",\n'.format(k, self.vocab[k]))
        self.out.write("}\n")

        self.out.write("_rvocab = {\n")
        for k in sorted(self.vocab.keys()):
            self.out.write(u'    "{}": "{}",\n'.format(self.vocab[k], k))
        self.out.write("}\n\n")

        for _, collected_type in self.collected_types.items():
            self.out.write("{} = {}\n".format(collected_type.name, collected_type.init))
        self.out.write("\n")

        self.out.write(
            """
def load_document(doc, baseuri=None, loadingOptions=None):
    # type: (Any, Optional[str], Optional[LoadingOptions]) -> Any
    if baseuri is None:
        baseuri = file_uri(os.getcwd()) + "/"
    if loadingOptions is None:
        loadingOptions = LoadingOptions()
    return _document_load(%(name)s, doc, baseuri, loadingOptions)


def load_document_by_string(string, uri, loadingOptions=None):
    # type: (Any, str, Optional[LoadingOptions]) -> Any
    result = yaml.main.round_trip_load(string, preserve_quotes=True)
    add_lc_filename(result, uri)

    if loadingOptions is None:
        loadingOptions = LoadingOptions(fileuri=uri)
    loadingOptions.idx[uri] = result

    return _document_load(%(name)s, result, uri, loadingOptions)
"""
            % dict(name=root_loader.name)
        )
