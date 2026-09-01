# target-ai/tests/test_rag.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import tempfile, pytest

chromadb = pytest.importorskip("chromadb")


def test_rag_returns_empty_on_empty_collection():
    with tempfile.TemporaryDirectory() as d:
        from chromadb.utils import embedding_functions
        import rag
        rag._collection = None
        rag._client = None
        client = chromadb.PersistentClient(path=d)
        ef = embedding_functions.DefaultEmbeddingFunction()
        col = client.get_or_create_collection("test_empty", embedding_function=ef)
        rag._collection = col
        result = rag.query_rag("anything", "test_empty")
        assert result == ""


def test_rag_add_and_query():
    with tempfile.TemporaryDirectory() as d:
        from chromadb.utils import embedding_functions
        import rag
        rag._collection = None
        rag._client = None
        client = chromadb.PersistentClient(path=d)
        ef = embedding_functions.DefaultEmbeddingFunction()
        col = client.get_or_create_collection("test_add", embedding_function=ef)
        rag._collection = col
        rag.add_document("Our refund policy is 30 days.", "doc1", "test_add")
        result = rag.query_rag("refund", "test_add")
        assert "refund" in result.lower()
