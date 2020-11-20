from typing import Union, Optional, Sequence

from ..document import Document
from ..querylang import QueryLang
from ...drivers import BaseDriver
from ...enums import CompressAlgo, ClientMode
from ...helper import typename, get_random_identity
from ...proto import jina_pb2

_trigger_body_fields = set(kk
                           for v in [jina_pb2.RequestProto.IndexRequestProto,
                                     jina_pb2.RequestProto.SearchRequestProto,
                                     jina_pb2.RequestProto.TrainRequestProto,
                                     jina_pb2.RequestProto.ControlRequestProto]
                           for kk in v.DESCRIPTOR.fields_by_name.keys())
_trigger_req_fields = set(jina_pb2.RequestProto.DESCRIPTOR.fields_by_name.keys()).difference(
    {'train', 'index', 'search', 'control'})
_trigger_fields = _trigger_req_fields.union(_trigger_body_fields)
_empty_request = jina_pb2.RequestProto()

AcceptQuerySetType = Union[QueryLang, BaseDriver, jina_pb2.QueryLangProto,
                           Sequence[QueryLang], Sequence[BaseDriver],
                           Sequence[jina_pb2.QueryLangProto]]


class Request:
    """
    :class:`Request` is one of the **primitive data type** in Jina.

    It offers a Pythonic interface to allow users access and manipulate
    :class:`jina.jina_pb2.RequestProto` object without working with Protobuf itself.

    A container for serialized :class:`jina_pb2.RequestProto` that only triggers deserialization
    and decompression when receives the first read access to its member.

    It overrides :meth:`__getattr__` to provide the same get/set interface as an
    :class:`jina_pb2.RequestProto` object.

    """

    def __init__(self, request: Union[bytes, 'jina_pb2.RequestProto', None] = None,
                 envelope: Optional['jina_pb2.EnvelopeProto'] = None,
                 copy: bool = False):

        self._buffer = None
        self._request = None  # type: 'jina_pb2.RequestProto'

        if isinstance(request, bytes):
            self._buffer = request
        elif isinstance(request, jina_pb2.RequestProto):
            if copy:
                self._request.CopyFrom(request)
            else:
                self._request = request
        elif request is None:
            self._request = jina_pb2.RequestProto()
            # make sure every new request has a request id
            self._request.request_id = get_random_identity()

        self._envelope = envelope
        self.is_used = False  #: Return True when request has been r/w at least once

    def __getattr__(self, name: str):
        # https://docs.python.org/3/reference/datamodel.html#object.__getattr__
        if name in _trigger_body_fields:
            req = getattr(self.as_pb_object, self.as_pb_object.WhichOneof('body'))
            return getattr(req, name)
        elif hasattr(_empty_request, name):
            return getattr(self.as_pb_object, name)
        else:
            raise AttributeError

    @staticmethod
    def _decompress(data: bytes, algorithm: str) -> bytes:
        if not algorithm:
            return data

        ctag = CompressAlgo.from_string(algorithm)
        if ctag == CompressAlgo.LZ4:
            import lz4.frame
            data = lz4.frame.decompress(data)
        elif ctag == CompressAlgo.BZ2:
            import bz2
            data = bz2.decompress(data)
        elif ctag == CompressAlgo.LZMA:
            import lzma
            data = lzma.decompress(data)
        elif ctag == CompressAlgo.ZLIB:
            import zlib
            data = zlib.decompress(data)
        elif ctag == CompressAlgo.GZIP:
            import gzip
            data = gzip.decompress(data)
        return data

    @property
    def as_pb_object(self) -> 'jina_pb2.RequestProto':
        """
        Cast ``self`` to a :class:`jina_pb2.RequestProto`. This will trigger
         :attr:`is_used`. Laziness will be broken and serialization will be recomputed when calling
         :meth:`SerializeToString`.
        """
        if self._request:
            # if request is already given while init
            self.is_used = True
            return self._request
        else:
            # if not then build one from buffer
            r = jina_pb2.RequestProto()
            _buffer = self._decompress(self._buffer, self._envelope.compression.algorithm)
            r.ParseFromString(_buffer)
            self.is_used = True
            self._request = r
            # # Though I can modify back the envelope, not sure if it is a good design:
            # # My intuition is: if the content is changed dramatically, e.g. from index to control request,
            # # then whatever writes on the envelope should be dropped
            # # depreciated. The only reason to reuse the envelope is saving cost on Envelope(), which is
            # # really a minor minor (and evil) optimization.
            # if self._envelope:
            #     self._envelope.request_type = getattr(r, r.WhichOneof('body')).__class__.__name__
            return r

    def SerializeToString(self):
        if self.is_used:
            return self.as_pb_object.SerializeToString()
        else:
            # no touch, skip serialization, return original
            return self._buffer

    def add_document(self, document: 'Document', mode: 'ClientMode'):
        """Add a document to the request """
        _req = getattr(self.as_pb_object, str(mode).lower())
        d = _req.docs.add()
        d.CopyFrom(document.as_pb_object)

    def add_groundtruth(self, document: 'Document', mode: 'ClientMode'):
        """Add a groundtruth document to the request """
        _req = getattr(self.as_pb_object, str(mode).lower())
        d = _req.groundtruths.add()
        d.CopyFrom(document.as_pb_object)

    def extend_queryset(self, queryset: AcceptQuerySetType):
        """Extend the queryset of the request """
        if not isinstance(queryset, Sequence):
            queryset = [queryset]
        for q in queryset:
            q_pb = self.as_pb_object.queryset.add()
            if isinstance(q, BaseDriver):
                q_pb.CopyFrom(QueryLang(q).as_pb_object)
            elif isinstance(q, jina_pb2.QueryLangProto):
                q_pb.CopyFrom(q)
            elif isinstance(q, QueryLang):
                q_pb.CopyFrom(q.as_pb_object)
            else:
                raise TypeError(f'unknown type {typename(q)}')
