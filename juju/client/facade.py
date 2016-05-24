import argparse
import builtins
import functools
import json
import keyword
from pathlib import Path
import pprint
import textwrap
from typing import Sequence, Mapping, TypeVar, Any, Union, Optional
import typing

from . import codegen

_marker = object()

# Map basic types to Python's typing with a callable
SCHEMA_TO_PYTHON = {
    'string': str,
    'integer': int,
    'float': float,
    'number': float,
    'boolean': bool,
    'object': Any,
}


class KindRegistry(dict):
    def register(self, name, version, obj):
        self[name] = {version: {
            "object": obj,
        }}

    def lookup(self, name, version=None):
        """If version is omitted, max version is used"""
        versions = self.get(name)
        if not versions:
            return None
        if version:
            return versions[version]
        return versions[max(versions)]

    def getObj(self, name, version=None):
        result = self.lookup(name, version)
        if result:
            obj = result["object"]
            return obj
        return None


class TypeRegistry(dict):
    def get(self, name):
        # Two way mapping
        refname = Schema.referenceName(name)
        if refname not in self:
            result = TypeVar(refname)
            self[refname] = result
            self[result] = refname

        return self[refname]

_types = TypeRegistry()
_registry = KindRegistry()
classes = {}

def booler(v):
    if isinstance(v, str):
        if v == "false":
            return False
    return bool(v)


def getRefType(ref):
    return _types.get(ref)


def refType(obj):
    return getRefType(obj["$ref"])


def objType(obj):
    kind = obj.get('type')
    if not kind:
        raise ValueError("%s has no type" % obj)
    result = SCHEMA_TO_PYTHON.get(kind)
    if not result:
        raise ValueError("%s has type %s" % (obj, kind))
    return result


basic_types = [str, bool, int, float]


def name_to_py(name):
    result = name.replace("-", "_")
    result = result.lower()
    if keyword.iskeyword(result) or result in dir(builtins):
        result += "_"
    return result


def strcast(kind, keep_builtins=False):
    if issubclass(kind, typing.GenericMeta):
        return str(kind)[1:]
    if (kind in basic_types or
            type(kind) in basic_types) and keep_builtins is False:
        return kind.__name__
    return kind


class Args(list):
    def __init__(self, defs):
        self.defs = defs
        #self.append("self")
        if defs:
            rtypes = _registry.getObj(_types[defs])
            if len(rtypes) == 1:
                if not self.do_explode(rtypes[0][1]):
                    for name, rtype in rtypes:
                        self.append((name, rtype))
            else:
                for name, rtype in rtypes:
                    self.append((name, rtype))

    def do_explode(self, kind):
        if kind in basic_types:
            return False
        if not issubclass(kind, (typing.Sequence,
                                 typing.Mapping)):
            self.clear()
            self.extend(Args(kind))
            return True
        return False

    def PyToSchemaMapping(self):
        m = {}
        for n, rt in self:
            m[name_to_py(n)] = n
        return m

    def SchemaToPyMapping(self):
        m = {}
        for n, tr in self:
            m[n] = name_to_py(n)
        return m

    def _format(self, name, rtype, typed=True):
        if typed:
            return "{} : {}".format(
                name_to_py(name),
                strcast(rtype)
            )
        else:
            return name_to_py(name)

    def _get_arg_str(self, typed=False, joined=", "):
        if self:
            parts = []
            for item in self:
                parts.append(self._format(item[0], item[1], typed))
            if joined:
                return joined.join(parts)
            return parts
        return ''

    def typed(self):
        return self._get_arg_str(True)

    def __str__(self):
        return self._get_arg_str(False)

    def get_doc(self):
        return self._get_arg_str(True, "\n")


def buildTypes(schema, capture):
    global classes
    INDENT = "    "
    for kind in sorted((k for k in _types if not isinstance(k, str)),
                       key=lambda x: str(x)):
        name = _types[kind]
        args = Args(kind)
        if name in classes:
            continue
        source = ["""
class {}(Type):
    _toSchema = {}
    _toPy = {}
    def __init__(self{}{}):
        '''
{}
        '''""".format(name,
                             args.PyToSchemaMapping(),
                             args.SchemaToPyMapping(),
                             ", " if args else "",
                             args,
                             textwrap.indent(args.get_doc(), INDENT *2))
         #pprint.pformat(schema['definitions'][name]))
                  ]
        assignments = args._get_arg_str(False, False)
        for assign in assignments:
            source.append("{}self.{} = {}".format(INDENT * 2, assign, assign))
        if not assignments:
            source.append("{}pass".format(INDENT *2))
        source = "\n".join(source)
        capture.write(source)
        capture.write("\n\n")
        co = compile(source, __name__, "exec")
        ns = _getns()
        exec(co, ns)
        cls = ns[name]
        classes[name] = cls


def retspec(defs):
    # return specs
    # only return 1, so if there is more than one type
    # we need to include a union
    # In truth there is only 1 return
    # Error or the expected Type
    if not defs:
        return None
    rtypes = _registry.getObj(_types[defs])
    if not rtypes:
        return None
    if len(rtypes) > 1:
        return Union[tuple([strcast(r[1], True) for r in rtypes])]
    return strcast(rtypes[0][1], False)


def return_type(defs):
    if not defs:
        return None
    rtypes = _registry.getObj(_types[defs])
    if not rtypes:
        return None
    if len(rtypes) > 1:
        for n, t in rtypes:
            if n == "Error":
                continue
            return t
    return rtypes[0][1]


def type_anno_func(func, defs, is_result=False):
    annos = {}
    if not defs:
        return func
    rtypes = _registry.getObj(_types[defs])
    if is_result:
        kn = "return"
        if not rtypes:
            annos[kn] = None
        elif len(rtypes) > 1:
            annos[kn] = Union[tuple([r[1] for r in rtypes])]
        else:
            annos[kn] = rtypes[0][1]
    else:
        for name, rtype in rtypes:
            name = name_to_py(name)
            annos[name] = rtype
    func.__annotations__.update(annos)
    return func


def ReturnMapping(cls):
    # Annotate the method with a return Type
    # so the value can be cast
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            reply = f(*args, **kwargs)
            if cls is None or reply:
                return reply
            if 'Error' in reply:
                cls = Error
            if issubclass(cls, typing.Sequence):
                result = []
                for item in reply:
                    result.append(cls.from_json(item))
            else:
                result = cls.from_json(reply)

            return result
        return wrapper
    return decorator


def makeFunc(cls, name, params, result, async=True):
    INDENT = "    "
    args = Args(params)
    assignments = []
    toschema = args.PyToSchemaMapping()
    for arg in args._get_arg_str(False, False):
        assignments.append("{}params[\'{}\'] = {}".format(INDENT,
                                                          toschema[arg],
                                                          arg))
    assignments = "\n".join(assignments)
    res = retspec(result)
    source = """

#@ReturnMapping({rettype})
{async}def {name}(self{argsep}{args}):
    '''
{docstring}
    Returns -> {res}
    '''
    # map input types to rpc msg
    params = dict()
    msg = dict(Type='{cls.name}', Request='{name}', Version={cls.version}, Params=params)
{assignments}
    reply = {await}self.rpc(msg)
    return self._map(reply, {name})

"""

    fsource = source.format(async="async " if async else "",
                            name=name,
                            argsep=", " if args else "",
                            args=args,
                            #ressep= " -> " if res else "",
                            res=res,
                            rettype=result.__name__ if result else None,
                            docstring=textwrap.indent(args.get_doc(), INDENT),
                            cls=cls,
                            assignments=assignments,
                            await="await " if async else "")
    ns = _getns()
    exec(fsource, ns)
    func = ns[name]
    return func, fsource


def buildMethods(cls, capture):
    properties = cls.schema['properties']
    for methodname in sorted(properties):
        method, source = _buildMethod(cls, methodname)
        setattr(cls, methodname, method)
        capture.write(source, depth=1)


def _buildMethod(cls, name):
    params = None
    result = None
    method = cls.schema['properties'][name]
    if 'properties' in method:
        prop = method['properties']
        spec = prop.get('Params')
        if spec:
            params = _types.get(spec['$ref'])
        spec = prop.get('Result')
        if spec:
            result = _types.get(spec['$ref'])
    return makeFunc(cls, name, params, result)


def buildFacade(schema):
    cls = type(schema.name, (Type,), dict(name=schema.name,
                                          version=schema.version,
                                          schema=schema))
    source = """
class {name}(Type):
    name = '{name}'
    version = {version}
    schema = {schema}
    """.format(name=schema.name,
               version=schema.version,
               schema=textwrap.indent(pprint.pformat(schema), "    "))
    return cls, source


class Type:
    def connect(self, connection):
        self.connection = connection

    async def rpc(self, msg):
        result = await self.connection.rpc(msg)
        return result

    def _map(self, reply, method):
        # Error, expected return or None
        if not reply:
            return None

        if 'Error' in reply:
            retcls = classes['Error']
            data = reply['Error']
            classes["Error"]
        elif 'Response' in reply:
            retcls = method.__return_type__
            data = reply['Response']
        return retcls.from_json(data)

    @classmethod
    def from_json(cls, data):
        if isinstance(data, str):
            data = json.loads(data)
        return cls(**data)

    def serialize(self):
        d = {}
        for attr, tgt in self._toSchema.items():
            d[tgt] = getattr(self, attr)
        return d

    def to_json(self):
        return json.dumps(self.serialize())


class Schema(dict):
    def __init__(self, schema):
        self.name = schema['Name']
        self.version = schema['Version']
        self.update(schema['Schema'])

    @classmethod
    def referenceName(cls, ref):
        if ref.startswith("#/definitions/"):
            ref = ref.rsplit("/", 1)[-1]
        return ref

    def resolveDefinition(self, ref):
        return self['definitions'][self.referenceName(ref)]

    def deref(self, prop, name):
        if not isinstance(prop, dict):
            raise TypeError(prop)
        if "$ref" not in prop:
            return prop

        target = self.resolveDefinition(prop["$ref"])
        return target

    def buildDefinitions(self):
        # here we are building the types out
        # anything in definitions is a type
        # but these may contain references themselves
        # so we dfs to the bottom and build upwards
        # when a types is already in the registry
        defs = self.get('definitions')
        if not defs:
            return
        for d, data in defs.items():
            if d in _registry:
                continue
            node = self.deref(data, d)
            kind = node.get("type")
            if kind == "object":
                result = self.buildObject(node, d)
            elif kind == "array":
                pass
            _registry.register(d, self.version, result)

    def buildObject(self, node, name=None, d=0):
        # we don't need to build types recursively here
        # they are all in definitions already
        # we only want to include the type reference
        # which we can derive from the name
        struct = []
        add = struct.append
        props = node.get("properties")
        pprops = node.get("patternProperties")
        if props:
            for p, prop in props.items():
                if "$ref" in prop:
                    add((p, refType(prop)))
                else:
                    kind = prop['type']
                    if kind == "array":
                        add((p, self.buildArray(prop, d + 1)))
                    elif kind == "object":
                        struct.extend(self.buildObject(prop, p, d + 1))
                    else:
                        add((p, objType(prop)))
        if pprops:
            if ".*" not in pprops:
                raise ValueError(
                    "Cannot handle actual pattern in patterProperties %s" %
                    pprops)
            pprop = pprops[".*"]
            if "$ref" in pprop:
                add((name, Mapping[str, refType(pprop)]))
                return struct
            ppkind = pprop["type"]
            if ppkind == "array":
                add((name, self.buildArray(pprop, d + 1)))
            else:
                add((name, Mapping[str, SCHEMA_TO_PYTHON[ppkind]]))
            #print("{}{}".format(d * "   ", struct))
        return struct

    def buildArray(self, obj, d=0):
        # return a sequence from an array in the schema
        if "$ref" in obj:
            return Sequence[refType(obj)]
        else:
            kind = obj.get("type")
            if kind and kind == "array":
                items = obj['items']
                return self.buildArray(items, d+1)
            else:
                return Sequence[objType(obj)]


def _getns():
    ns = {'Type': Type,
          'typing': typing,
          'ReturnMapping': ReturnMapping
          }
    # Copy our types into the globals of the method
    for facade in _registry:
        ns[facade] = _registry.getObj(facade)
    return ns



def generate_facacdes(options):
    global classes
    schemas = json.loads(Path(options.schema).read_text("utf-8"))
    capture = codegen.CodeWriter()
    capture.write("""
from juju.client.facade import Type, ReturnMapping
                  """)
    schemas = [Schema(s) for s in schemas]

    for schema in schemas:
        schema.buildDefinitions()
        buildTypes(schema, capture)

    for schema in schemas:
        # TODO generate class now with a metaclass that takes the schema
        # the generated class has the right name and it in turn uses
        # the metaclass to populate cls
        cls, source = buildFacade(schema)
        capture.write(source)
        buildMethods(cls, capture)
        classes[schema.name] = cls

    return capture

def setup():
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--schema", default="schemas.json")
    parser.add_argument("-o", "--output", default="client.py")
    options = parser.parse_args()
    return options

def main():
    options = setup()
    capture = generate_facacdes(options)
    with open(options.output, "w") as fp:
        print(capture, file=fp)



if __name__ == '__main__':
    main()