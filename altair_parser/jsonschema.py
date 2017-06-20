import jinja2
import os

from .utils import construct_function_call, Variable


OBJECT_TEMPLATE = """
{%- for import in cls.imports %}
{{ import }}
{%- endfor %}

class {{ cls.classname }}({{ cls.baseclass }}):
    {%- for (name, prop) in cls.wrapped_properties().items() %}
    {{ name }} = {{ prop.trait_code }}
    {%- endfor %}
"""

class JSONSchema(object):
    """A class to wrap JSON Schema objects and reason about their contents"""
    object_template = OBJECT_TEMPLATE
    __draft__ = 4

    _cached_references = {}
    simple_types = ["boolean", "null", "number", "integer", "string"]
    valid_types = simple_types + ["array", "object"]
    traitlet_map = {'array': {'cls': 'jst.JSONArray'},
                    'boolean': {'cls': 'jst.JSONBoolean'},
                    'null': {'cls': 'jst.JSONNull'},
                    'number': {'cls': 'jst.JSONNumber'},
                    'integer': {'cls': 'jst.JSONInteger'},
                    'string': {'cls': 'jst.JSONString'},
                   }
    attr_defaults = {'title': '',
                     'description': '',
                     'properties': {},
                     'definitions': {},
                     'default': None,
                     'examples': {},
                     'type': 'object'}
    basic_imports = ["import traitlets as T",
                     "from . import jstraitlets as jst",
                     "from .baseobject import BaseObject"]

    def __init__(self, schema, context=None, parent=None, name=None):
        self.schema = schema
        self.parent = parent
        self.name = name

        # if context is not given, then assume this is a root instance that
        # defines its context
        self.context = context or schema

    def make_child(self, schema, name=None):
        """
        Make a child instance, appropriately defining the parent and context
        """
        return self.__class__(schema, context=self.context,
                              parent=self, name=name)

    def __getattr__(self, attr):
        if attr in self.attr_defaults:
            return self.schema.get(attr, self.attr_defaults[attr])
        raise AttributeError(f"'{self.__class__.__name__}' object "
                             f"has no attribute '{attr}'")

    @property
    def is_root(self):
        return self.context is self.schema

    @property
    def is_trait(self):
        return self.type != 'object' and not self.is_reference

    @property
    def is_object(self):
        return self.type == 'object' and not self.is_reference

    @property
    def is_reference(self):
        return '$ref' in self.schema

    @property
    def classname(self):
        if self.name:
            return self.name
        elif self.is_root:
            return "RootInstance"
        elif self.is_reference:
            return self.schema['$ref'].split('/')[-1]
        else:
            raise NotImplementedError("Anonymous class name")

    @property
    def modulename(self):
        return self.classname.lower()

    @property
    def filename(self):
        return self.modulename + '.py'

    @property
    def baseclass(self):
        return "BaseObject"

    @property
    def import_statement(self):
        return f"from .{self.modulename} import {self.classname}"

    @property
    def imports(self):
        imports = []
        imports.extend(self.basic_imports)
        for obj in self.wrapped_properties().values():
            if obj.is_reference:
                ref = self.get_reference(obj.schema['$ref'])
                if ref.is_object:
                    imports.append(ref.import_statement)
        return imports

    @property
    def module_imports(self):
        imports = []
        for obj in self.wrapped_definitions().values():
            if obj.is_object:
                imports.append(obj.import_statement)
        return imports

    def wrapped_definitions(self):
        """Return definition dictionary wrapped as JSONSchema objects"""
        return {name.lower(): self.make_child(schema, name=name)
                for name, schema in self.definitions.items()}

    def wrapped_properties(self):
        """Return property dictionary wrapped as JSONSchema objects"""
        return {name: self.make_child(val)
                for name, val in self.properties.items()}

    def get_reference(self, ref, cache=True):
        """
        Get the JSONSchema object for the given reference code.

        Reference codes should look something like "#/definitions/MyDefinition"

        By default, this will cache objects accessed by their ref code.
        """
        if cache and ref in self._cached_references:
            return self._cached_references[ref]

        path = ref.split('/')
        name = path[-1]
        if path[0] != '#':
            raise ValueError(f"Unrecognized $ref format: '{ref}'")
        try:
            schema = self.context
            for key in path[1:]:
                schema = schema[key]
        except KeyError:
            raise ValueError(f"$ref='{ref}' not present in the schema")

        wrapped_schema = self.make_child(schema, name=name)
        if cache:
            self._cached_references[ref] = wrapped_schema
        return wrapped_schema

    @property
    def trait_code(self):
        """Create the trait code for the given typecode"""
        typecode = self.type

        # TODO: check how jsonschema handles multiple entries...
        #       e.g. anyOf + enum or $ref + oneOf

        if "not" in self.schema:
            raise NotImplementedError("'not' keyword")
        elif "$ref" in self.schema:
            # TODO: handle other properties in schema, maybe via allOf?
            ref = self.get_reference(self.schema['$ref'])
            if ref.is_object:
                return f'T.Instance({ref.classname})'
            else:
                return ref.trait_code
        elif "anyOf" in self.schema:
            raise NotImplementedError("'anyOf' keyword")
        elif "allOf" in self.schema:
            raise NotImplementedError("'allOf' keyword")
        elif "oneOf" in self.schema:
            raise NotImplementedError("'oneOf' keyword")
        elif "enum" in self.schema:
            return construct_function_call('jst.JSONEnum', self.schema["enum"])
        elif typecode in self.simple_types:
            # TODO: implement checks like maximum, minimum, format, etc.
            info = self.traitlet_map[typecode]
            return construct_function_call(info['cls'],
                                           *info.get('args', []),
                                           **info.get('kwargs', {}))
        elif typecode == 'array':
            # TODO: implement checks like maxLength, minLength, etc.
            items = self.schema['items']
            if isinstance(items, list):
                # TODO: need to implement this in the JSONArray traitlet
                # Also need to check value of "additionalItems"
                raise NotImplementedError("'items' keyword as list")
            else:
                itemtype = self.make_child(items).trait_code
            return construct_function_call('jst.JSONArray', Variable(itemtype))
        elif typecode == 'object':
            return construct_function_call('jst.JSONInstance', self.classname)
        elif isinstance(typecode, list):
            # TODO: if Null is in the list, then add keyword allow_none=True
            arg = "[{0}]".format(', '.join(self.make_child({'type':typ}).trait_code
                                           for typ in typecode))
            return construct_function_call('jst.JSONUnion', Variable(arg))
        else:
            raise ValueError(f"unrecognized type identifier: {typecode}")

    def object_code(self):
        return jinja2.Template(self.object_template).render(cls=self)

    def module_spec(self):
        assert self.is_root
        submodroot = self.classname.lower()

        modspec = {
            'jstraitlets.py': open(os.path.join(os.path.dirname(__file__),
                                   'jstraitlets.py')).read(),
            'baseobject.py': open(os.path.join(os.path.dirname(__file__),
                                  'baseobject.py')).read(),
            self.filename: self.object_code()
        }

        modspec['__init__.py'] = '\n'.join([self.import_statement]
                                            + self.module_imports)

        modspec.update({schema.filename: schema.object_code()
                        for schema in self.wrapped_definitions().values()
                        if schema.is_object})

        return modspec
