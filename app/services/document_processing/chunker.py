"""
Text chunking service for splitting documents into manageable pieces.
"""

import logging
import re

logger = logging.getLogger(__name__)


# Lazily-loaded tiktoken encoder (BPE encoding used by OpenAI models).
# Cached at module level so repeated chunker instantiations don't pay the
# encoding-load cost each time.
_TOKEN_ENCODER = None


def _get_token_encoder():
    """Return a cached tiktoken encoder for the cl100k_base BPE."""
    global _TOKEN_ENCODER
    if _TOKEN_ENCODER is None:
        try:
            import tiktoken

            _TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "tiktoken is required for token-mode chunking. "
                "Install with `poetry add tiktoken`."
            ) from exc
    return _TOKEN_ENCODER


class Chunker:
    """
    Splits text into chunks with configurable size and overlap.

    Two size modes:
      - mode='char' (default): chunk_size/overlap measured in characters.
        Backward compatible with existing callers.
      - mode='token': chunk_size/overlap measured in BPE tokens via tiktoken
        (cl100k_base). Used by the heatmap ingest pipeline because the
        downstream classifier and embedding model both bill by token.

    Strategies (orthogonal to mode):
      - 'fixed':     sliding window with word-boundary breaks
      - 'sentence':  sentence-aware aggregation
      - 'paragraph': paragraph-aware aggregation
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        mode: str = "char",
    ):
        """
        Initialize chunker.

        Args:
            chunk_size: Target size of each chunk (chars when mode='char',
                tokens when mode='token').
            chunk_overlap: Overlap between chunks (same unit as chunk_size).
            mode: 'char' (default) or 'token'.
        """
        if mode not in ("char", "token"):
            raise ValueError(f"Unknown chunk mode {mode!r}; use 'char' or 'token'")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.mode = mode
        logger.debug(
            f"Chunker initialized with mode={mode}, size={chunk_size}, overlap={chunk_overlap}"
        )

    def chunk_text(self, text: str, strategy: str = "fixed") -> list[str]:
        """
        Split text into chunks using specified strategy.

        Args:
            text: Text to chunk
            strategy: Chunking strategy ('fixed', 'sentence', 'paragraph')

        Returns:
            List of text chunks
        """
        if not text or not text.strip():
            logger.warning("Empty or whitespace-only text provided")
            return []

        if strategy == "fixed":
            if self.mode == "token":
                return self._chunk_fixed_tokens(text)
            return self._chunk_fixed(text)
        elif strategy == "sentence":
            return self._chunk_by_sentence(text)
        elif strategy == "paragraph":
            return self._chunk_by_paragraph(text)
        else:
            logger.warning(f"Unknown strategy '{strategy}', using 'fixed'")
            if self.mode == "token":
                return self._chunk_fixed_tokens(text)
            return self._chunk_fixed(text)

    def _chunk_fixed(self, text: str) -> list[str]:
        """
        Fixed-size chunking with overlap.
        Simple but effective for most use cases.
        """
        chunks = []
        start = 0
        text_length = len(text)

        while start < text_length:
            end = start + self.chunk_size

            # If this is not the last chunk, try to break at a word boundary
            if end < text_length:
                # Look for the last space within the chunk
                last_space = text.rfind(" ", start, end)
                if last_space > start + (
                    self.chunk_size // 2
                ):  # At least halfway through
                    end = last_space

            chunk = text[start:end].strip()
            if chunk:  # Only add non-empty chunks
                chunks.append(chunk)

            # Move start position, accounting for overlap
            prev_start = start
            start = end - self.chunk_overlap

            # Prevent infinite loop if chunk_overlap >= chunk_size
            if start <= prev_start:
                start = end

        logger.info(f"Created {len(chunks)} chunks using fixed strategy")
        return chunks

    def _chunk_fixed_tokens(self, text: str) -> list[str]:
        """
        Fixed-size token chunking with overlap using tiktoken BPE.

        Splits the text at token boundaries, then maps each chunk's token
        slice back to the original substring. Word-boundary snapping is
        not needed because BPE tokens already end at natural text
        boundaries (a single English word is typically 1-2 BPE tokens).
        """
        enc = _get_token_encoder()
        tokens = enc.encode(text)
        total = len(tokens)
        chunks: list[str] = []
        start = 0

        while start < total:
            end = min(start + self.chunk_size, total)
            token_slice = tokens[start:end]
            chunk_text = enc.decode(token_slice).strip()
            if chunk_text:
                chunks.append(chunk_text)

            prev_start = start
            start = end - self.chunk_overlap
            # Guard against overlap >= chunk_size causing an infinite loop.
            if start <= prev_start:
                start = end

        logger.info(f"Created {len(chunks)} chunks using token-fixed strategy")
        return chunks

    def _chunk_by_sentence(self, text: str) -> list[str]:
        """
        Chunk by sentences, respecting chunk_size limits.
        Better for maintaining semantic coherence.
        """
        # Split into sentences (basic regex)
        sentences = re.split(r"(?<=[.!?])\s+", text)

        chunks = []
        current_chunk = []
        current_size = 0

        for sentence in sentences:
            sentence_size = len(sentence)

            # A single sentence longer than chunk_size (OCR noise, no
            # terminal punctuation, one giant run-on) can't be packed as a
            # unit. Flush whatever's buffered, then fall back to fixed-window
            # splitting for just this sentence so no chunk is left unbounded.
            if sentence_size > self.chunk_size:
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                    current_size = 0
                chunks.extend(self._chunk_fixed(sentence))
                continue

            # If adding this sentence would exceed chunk_size, start new chunk
            if current_size + sentence_size > self.chunk_size and current_chunk:
                chunks.append(" ".join(current_chunk))

                # Keep last few sentences for overlap
                overlap_sentences = []
                overlap_size = 0
                for s in reversed(current_chunk):
                    if overlap_size + len(s) <= self.chunk_overlap:
                        overlap_sentences.insert(0, s)
                        overlap_size += len(s)
                    else:
                        break

                current_chunk = overlap_sentences
                current_size = overlap_size

            current_chunk.append(sentence)
            current_size += sentence_size

        # Add remaining chunk
        if current_chunk:
            chunks.append(" ".join(current_chunk))

        logger.info(f"Created {len(chunks)} chunks using sentence strategy")
        return chunks

    def _chunk_by_paragraph(self, text: str) -> list[str]:
        """
        Chunk by paragraphs, respecting chunk_size limits.
        Best for structured documents.
        """
        # Split into paragraphs
        paragraphs = re.split(r"\n\s*\n", text)

        chunks = []
        current_chunk = []
        current_size = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_size = len(para)

            # If paragraph is larger than chunk_size, split it
            if para_size > self.chunk_size:
                # Save current chunk if exists
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = []
                    current_size = 0

                # Split large paragraph using fixed chunking
                para_chunks = self._chunk_fixed(para)
                chunks.extend(para_chunks)
                continue

            # If adding this paragraph would exceed chunk_size, start new chunk
            if current_size + para_size > self.chunk_size and current_chunk:
                chunks.append("\n\n".join(current_chunk))

                # Keep last paragraph for overlap if it fits
                if len(current_chunk[-1]) <= self.chunk_overlap:
                    current_chunk = [current_chunk[-1]]
                    current_size = len(current_chunk[-1])
                else:
                    current_chunk = []
                    current_size = 0

            current_chunk.append(para)
            current_size += para_size

        # Add remaining chunk
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        logger.info(f"Created {len(chunks)} chunks using paragraph strategy")
        return chunks
