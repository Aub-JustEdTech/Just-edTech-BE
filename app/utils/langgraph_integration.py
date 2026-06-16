"""
LangGraph integration for building RAG workflows.
"""

import asyncio
from typing import Any

try:
    from langchain.schema import AIMessage, BaseMessage, HumanMessage, SystemMessage
    from langgraph.graph import Graph, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    # Fallback types
    Graph = None
    StateGraph = None
    BaseMessage = None
    HumanMessage = None
    AIMessage = None
    SystemMessage = None

from app.schemas.citations import CitationCreate
from app.services.llm_service import LLMService
from app.utils.rag import rag_processor


class RAGState:
    """State class for RAG workflow"""

    def __init__(self):
        self.query: str = ""
        self.conversation_history: list[dict] = []
        self.retrieved_documents: list[dict] = []
        self.context: str = ""
        self.response: str = ""
        self.citations: list[CitationCreate] = []
        self.tenant_id: int = 0


def build_rag_graph() -> Any | None:
    """Build LangGraph workflow definition"""
    if not LANGGRAPH_AVAILABLE:
        return None

    def retrieve_documents(state: RAGState) -> RAGState:
        """Retrieve relevant documents"""
        # Get tenant's document IDs (placeholder)
        document_ids = [1, 2, 3]  # This should come from database

        # Query documents
        search_results = rag_processor.query_documents(
            state.query, document_ids, top_k=5
        )

        state.retrieved_documents = search_results
        return state

    def format_context(state: RAGState) -> RAGState:
        """Format context from conversation history and retrieved documents"""
        # Format conversation history
        history_context = ""
        for msg in state.conversation_history[-10:]:  # Last 10 messages
            role = msg.get("role", "").upper()
            content = msg.get("content", "")
            history_context += f"{role}: {content}\n"

        # Format retrieved documents
        doc_context = "\n\n".join(
            [
                f"Document {doc['document_id']}: {doc['chunk']}"
                for doc in state.retrieved_documents
            ]
        )

        state.context = f"""
Conversation History:
{history_context}

Retrieved Documents:
{doc_context}

Current Question: {state.query}
"""
        return state

    def generate_response(state: RAGState) -> RAGState:
        """Generate response using LLM"""
        system_prompt = """You are a helpful AI assistant. Use the provided context from documents and conversation history to answer the user's question. If you reference information from the documents, be specific about which document you're referencing."""

        try:
            # Use LLMService to properly handle model-specific parameters
            llm_service = LLMService()

            # Run async function in sync context (LangGraph requires sync functions)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(
                    llm_service.generate_chat_completion(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": state.context},
                        ],
                        model="gpt-4o-mini",  # Use modern default model
                        temperature=0.7,
                        max_tokens=4000,
                    )
                )
                state.response = response["content"]
            finally:
                loop.close()
        except Exception as e:
            state.response = f"I apologize, but I encountered an error: {str(e)}"

        return state

    def extract_citations(state: RAGState) -> RAGState:
        """Extract citations from retrieved documents"""
        citations = []

        for i, doc in enumerate(state.retrieved_documents):
            citation = CitationCreate(
                document_title=f"Document {doc.get('document_id')}",
                document_url=f"/documents/{doc.get('document_id')}",
                snippet=doc.get("chunk", "")[:500] + "..."
                if len(doc.get("chunk", "")) > 500
                else doc.get("chunk", ""),
                position=i + 1,
            )
            citations.append(citation)

        state.citations = citations
        return state

    # Build the graph
    workflow = StateGraph(RAGState)

    # Add nodes
    workflow.add_node("retrieve", retrieve_documents)
    workflow.add_node("format_context", format_context)
    workflow.add_node("generate", generate_response)
    workflow.add_node("extract_citations", extract_citations)

    # Add edges
    workflow.add_edge("retrieve", "format_context")
    workflow.add_edge("format_context", "generate")
    workflow.add_edge("generate", "extract_citations")

    # Set entry point
    workflow.set_entry_point("retrieve")
    workflow.set_finish_point("extract_citations")

    return workflow.compile()


def invoke_rag_with_context(
    query: str, conversation_history: list[Any], tenant_id: int
) -> tuple[str, list[CitationCreate]]:
    """Run LangGraph with conversation history"""
    if not LANGGRAPH_AVAILABLE:
        # Fallback to regular RAG processing
        from app.utils.rag import process_rag_query_with_citations

        return process_rag_query_with_citations(query, conversation_history, tenant_id)

    try:
        # Build and run the graph
        graph = build_rag_graph()
        if not graph:
            # Fallback
            from app.utils.rag import process_rag_query_with_citations

            return process_rag_query_with_citations(
                query, conversation_history, tenant_id
            )

        # Initialize state
        initial_state = RAGState()
        initial_state.query = query
        initial_state.conversation_history = [
            {"role": msg.role, "content": msg.content} for msg in conversation_history
        ]
        initial_state.tenant_id = tenant_id

        # Run the workflow
        final_state = graph.invoke(initial_state)

        return final_state.response, final_state.citations

    except Exception:
        # Fallback to regular processing
        from app.utils.rag import process_rag_query_with_citations

        return process_rag_query_with_citations(query, conversation_history, tenant_id)


def convert_to_langchain_messages(messages: list[dict]) -> list[Any]:
    """Convert message format to LangChain message format"""
    if not LANGGRAPH_AVAILABLE:
        return []

    langchain_messages = []

    for msg in messages:
        role = msg.get("role", "").lower()
        content = msg.get("content", "")

        if role == "user":
            langchain_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            langchain_messages.append(AIMessage(content=content))
        elif role == "system":
            langchain_messages.append(SystemMessage(content=content))

    return langchain_messages
