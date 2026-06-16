"""
Service for generating structured PDF reports from conversations.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime
from io import BytesIO
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.conversations import conversation as conversation_crud
from app.models.conversations import Conversation, Message
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)


class ConversationReportService:
    """Generate structured text summaries and PDF reports for conversations."""

    def __init__(self, max_transcript_chars: int = 8000) -> None:
        self.max_transcript_chars = max_transcript_chars

    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename to ensure it only contains ASCII characters safe for HTTP headers.
        
        This converts Unicode characters to their ASCII equivalents where possible,
        removes characters that can't be encoded in Latin-1, and ensures the filename
        is safe for use in Content-Disposition headers.
        
        Args:
            filename: The original filename
            
        Returns:
            Sanitized filename safe for HTTP headers
        """
        # First, replace common Unicode punctuation with ASCII equivalents
        replacements = {
            '\u2011': '-',  # Non-breaking hyphen
            '\u2012': '-',  # Figure dash
            '\u2013': '-',  # En dash
            '\u2014': '-',  # Em dash
            '\u2015': '-',  # Horizontal bar
            '\u2018': "'",  # Left single quotation mark
            '\u2019': "'",  # Right single quotation mark
            '\u201c': '"',  # Left double quotation mark
            '\u201d': '"',  # Right double quotation mark
            '\u2026': '...',  # Horizontal ellipsis
        }
        for unicode_char, ascii_char in replacements.items():
            filename = filename.replace(unicode_char, ascii_char)
        
        # Normalize Unicode characters to their closest ASCII equivalents
        # NFKD = Compatibility Decomposition, then filter out combining characters
        normalized = unicodedata.normalize('NFKD', filename)
        ascii_str = normalized.encode('ascii', 'ignore').decode('ascii')
        
        # Replace spaces and special characters with hyphens
        ascii_str = re.sub(r'[^\w\-.]', '-', ascii_str)
        
        # Remove multiple consecutive hyphens
        ascii_str = re.sub(r'-+', '-', ascii_str)
        
        # Remove leading/trailing hyphens
        ascii_str = ascii_str.strip('-')
        
        # Ensure we have a valid filename
        if not ascii_str:
            ascii_str = "conversation-report"
            
        return ascii_str

    async def get_conversation_for_report(
        self,
        db: AsyncSession,
        conversation_id: int,
    ) -> Conversation | None:
        """
        Load a conversation with all messages for reporting.
        """
        db_conversation = await conversation_crud.get_conversation_by_id(
            db, conversation_id
        )
        if not db_conversation:
            return None

        # Ensure messages are in chronological order
        db_conversation.messages.sort(key=lambda m: m.created_at)
        return db_conversation

    def build_transcript(self, messages: Iterable[Message]) -> str:
        """
        Build a normalized transcript string from messages, capped in length.
        """
        parts: list[str] = []
        current_length = 0

        # Use most recent messages if we exceed the limit
        for msg in reversed(list(messages)):
            role = msg.role.upper()
            content = msg.content or ""
            text = f"{role}: {content}\n"
            length = len(text)

            if current_length + length > self.max_transcript_chars:
                break

            parts.insert(0, text)
            current_length += length

        return "".join(parts)

    def _extract_citations(self, messages: Iterable[Message]) -> list[dict[str, Any]]:
        """
        Extract unique citations from all messages in the conversation.
        
        Returns:
            List of citation dictionaries with title, url, and snippet
        """
        citations_dict: dict[str, dict[str, Any]] = {}
        
        for msg in messages:
            if msg.role == "assistant" and hasattr(msg, "citations"):
                for citation in msg.citations:
                    # Use URL as unique key to avoid duplicates
                    url = citation.document_url
                    if url and url not in citations_dict:
                        citations_dict[url] = {
                            "title": citation.document_title or "Untitled Document",
                            "url": url,
                            "snippet": citation.snippet,
                            "page_number": citation.page_number,
                        }
        
        return list(citations_dict.values())

    async def generate_report_title(
        self,
        db: AsyncSession,
        chatbot_config_id: int,
        transcript: str,
    ) -> str:
        """
        Generate a concise title (max 5 words) for the report based on the conversation.
        """
        system_prompt = (
            "You are an AI assistant that creates concise, professional titles for conversation reports. "
            "Generate a title that captures the main topic or theme of the conversation.\n\n"
            "REQUIREMENTS:\n"
            "- Maximum 5 words\n"
            "- Professional and clear\n"
            "- Focus on the main topic\n"
            "- Do NOT include words like 'Report', 'Summary', 'Conversation'\n"
            "- Return ONLY the title, nothing else"
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Generate a concise title (max 5 words) for this conversation:\n\n{transcript[:1000]}"
            },
        ]

        result = await llm_service.generate_chat_completion_with_config(
            db=db,
            chatbot_config_id=chatbot_config_id,
            messages=messages,
        )

        title = str(result.get("content", "")).strip()
        # Ensure it's not too long
        words = title.split()
        if len(words) > 5:
            title = " ".join(words[:5])
        
        return title or "Conversation Report"

    async def generate_report_text(
        self,
        db: AsyncSession,
        chatbot_config_id: int,
        transcript: str,
    ) -> str:
        """
        Call the LLM to generate a structured report from a conversation transcript.
        
        This generates a high-level summary focusing on key topics and insights,
        not a verbatim transcript.
        """
        system_prompt = (
            "You are an AI assistant that creates professional, executive-style summary reports "
            "from conversation transcripts. Your goal is to provide a high-level overview that "
            "captures the essence and key insights of the discussion, NOT to repeat the conversation.\n\n"
            "Create a report with the following sections (use EXACTLY these section headings):\n\n"
            "EXECUTIVE SUMMARY\n"
            "Provide a concise 2-3 sentence overview of what was discussed and the main outcome.\n\n"
            "KEY TOPICS DISCUSSED\n"
            "List 3-5 main topics or themes that emerged using bullet points (•).\n"
            "Focus on substance, not who said what.\n"
            "Use **bold text** for important terms or phrases.\n\n"
            "MAIN INSIGHTS & FINDINGS\n"
            "Highlight important information, answers, or discoveries.\n"
            "What did the user learn or accomplish? Use bullet points for clarity.\n"
            "Use **bold text** for key insights.\n\n"
            "RECOMMENDATIONS & NEXT STEPS\n"
            "List action items, follow-ups, or suggestions mentioned (use bullet points).\n"
            "Only include this section if there are relevant recommendations.\n\n"
            "OPEN QUESTIONS\n"
            "List unresolved issues or topics that need further exploration (use bullet points).\n"
            "Only include this section if there are open questions.\n\n"
            "FORMATTING REQUIREMENTS:\n"
            "- Use section headings in ALL CAPS (e.g., EXECUTIVE SUMMARY)\n"
            "- Use bullet points (•) for lists, not dashes or asterisks\n"
            "- Use **text** for bold formatting (e.g., **important term**)\n"
            "- Add blank lines between sections for readability\n"
            "- Write in third person, professional tone\n"
            "- Focus on WHAT was discussed, not WHO said it\n"
            "- Be concise but informative\n"
            "- Do NOT include phrases like 'The user asked' or 'The assistant responded'\n"
            "- Do NOT include the raw transcript or conversation flow\n"
            "- Do NOT add any title or heading like '## Professional Summary Report' at the top"
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Analyze this conversation and create a professional summary report "
                    "following the structure provided. Focus on the substance and insights, "
                    "not the conversation flow.\n\n"
                    f"CONVERSATION:\n{transcript}"
                ),
            },
        ]

        result = await llm_service.generate_chat_completion_with_config(
            db=db,
            chatbot_config_id=chatbot_config_id,
            messages=messages,
        )

        content = str(result.get("content", "")).strip()
        if not content:
            return (
                "EXECUTIVE SUMMARY\n\n"
                "Unable to generate a detailed summary for this conversation.\n\n"
                "KEY TOPICS DISCUSSED\n\n"
                "• No topics identified"
            )
        return content

    def _convert_markdown_to_html(self, text: str) -> str:
        """
        Convert markdown-style text to HTML with proper formatting.
        Handles **bold** and bullet points.
        """
        # Convert **bold** to <strong>
        text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
        
        # Escape other HTML characters
        text = text.replace('&', '&amp;').replace('<strong>', '|||STRONG|||').replace('</strong>', '|||/STRONG|||')
        text = text.replace('<', '&lt;').replace('>', '&gt;')
        text = text.replace('|||STRONG|||', '<strong>').replace('|||/STRONG|||', '</strong>')
        
        return text

    def render_pdf_weasyprint(
        self,
        report_text: str,
        conversation: Conversation,
        citations: list[dict[str, Any]],
        report_title: str | None = None,
    ) -> BytesIO:
        """
        Render report text into a professionally formatted PDF document using WeasyPrint.
        
        Args:
            report_text: The generated report content
            conversation: The conversation object
            citations: List of citation dictionaries (not used, kept for compatibility)
            report_title: The generated report title
            
        Returns:
            BytesIO buffer containing the PDF
        """
        # Use generated report title (max 5 words)
        title = report_title or "Conversation Report"
        
        # Build HTML content
        html_parts = [
            '<!DOCTYPE html>',
            '<html>',
            '<head>',
            '<meta charset="UTF-8">',
            '<style>',
            '@page {',
            '    size: letter;',
            '    margin: 0.75in;',
            '}',
            'body {',
            '    font-family: "Helvetica", "Arial", sans-serif;',
            '    font-size: 10pt;',
            '    color: #333;',
            '    line-height: 1.5;',
            '}',
            'h1 {',
            '    font-size: 18pt;',
            '    color: #1a1a1a;',
            '    margin: 0 0 20px 0;',
            '    padding-bottom: 15px;',
            '    border-bottom: 1px solid #b3b3b3;',
            '    font-weight: bold;',
            '}',
            'h2 {',
            '    font-size: 13pt;',
            '    color: #2c5aa0;',
            '    margin: 20px 0 10px 0;',
            '    font-weight: bold;',
            '}',
            'p {',
            '    margin: 0 0 12px 0;',
            '    text-align: justify;',
            '}',
            'ul {',
            '    margin: 8px 0;',
            '    padding-left: 20px;',
            '}',
            'li {',
            '    margin: 6px 0;',
            '}',
            'strong {',
            '    font-weight: bold;',
            '    color: #1a1a1a;',
            '}',
            '</style>',
            '</head>',
            '<body>',
            f'<h1>{self._escape_html(title)}</h1>',
        ]
        
        # Parse report content
        lines = report_text.split("\n")
        current_section = []
        in_list = False
        
        for line in lines:
            line = line.strip()
            
            # Skip markdown headings like "## Professional Summary Report"
            if line.startswith("#"):
                continue
            
            if not line:
                # Empty line - close any open list
                if in_list:
                    html_parts.append('</ul>')
                    in_list = False
                continue
            
            # Check if line is a section heading (all caps)
            if line.isupper() and len(line) > 3:
                # Close any open list
                if in_list:
                    html_parts.append('</ul>')
                    in_list = False
                
                heading_text = line.rstrip(":")
                html_parts.append(f'<h2>{self._escape_html(heading_text)}</h2>')
            
            # Check for bullet points
            elif line.startswith("•") or line.startswith("-") or line.startswith("*"):
                bullet_text = line.lstrip("•-* ").strip()
                bullet_html = self._convert_markdown_to_html(bullet_text)
                
                if not in_list:
                    html_parts.append('<ul>')
                    in_list = True
                
                html_parts.append(f'<li>{bullet_html}</li>')
            
            # Check for numbered lists
            elif len(line) > 0 and line[0].isdigit() and len(line) > 2 and line[1:3] in (". ", ") "):
                # Close bullet list if open
                if in_list:
                    html_parts.append('</ul>')
                    in_list = False
                
                list_html = self._convert_markdown_to_html(line)
                html_parts.append(f'<p>{list_html}</p>')
            
            else:
                # Regular paragraph
                if in_list:
                    html_parts.append('</ul>')
                    in_list = False
                
                para_html = self._convert_markdown_to_html(line)
                html_parts.append(f'<p>{para_html}</p>')
        
        # Close any open list
        if in_list:
            html_parts.append('</ul>')
        
        html_parts.extend([
            '</body>',
            '</html>',
        ])
        
        # Generate HTML string
        html_string = '\n'.join(html_parts)
        
        # Generate PDF using WeasyPrint (lazy import to avoid loading at startup)
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_string).write_pdf()
        
        # Return as BytesIO buffer
        buffer = BytesIO(pdf_bytes)
        buffer.seek(0)
        
        return buffer
    
    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    
    # Alias for backwards compatibility
    render_pdf = render_pdf_weasyprint

    async def generate_report_pdf_for_conversation(
        self,
        db: AsyncSession,
        conversation_id: int,
    ) -> tuple[str, BytesIO]:
        """
        Generate a PDF report for a conversation and return filename + buffer.
        
        This creates a professional report with:
        - Executive summary and key topics (not raw transcript)
        - Professional formatting with sections and styling
        - Citations section at the end for references
        
        Args:
            db: Database session
            conversation_id: ID of the conversation to generate report for
            
        Returns:
            Tuple of (filename, pdf_buffer)
        """
        try:
            logger.info(f"Starting report generation for conversation {conversation_id}")
            
            db_conversation = await self.get_conversation_for_report(db, conversation_id)
            if not db_conversation:
                logger.error(f"Conversation {conversation_id} not found")
                raise ValueError("Conversation not found")

            if not db_conversation.chatbot_config_id:
                logger.error(f"Conversation {conversation_id} has no chatbot_config_id")
                raise ValueError("Conversation has no associated chatbot configuration")

            logger.info(f"Building transcript for conversation {conversation_id}")
            # Build transcript for LLM analysis
            transcript = self.build_transcript(db_conversation.messages)
            logger.info(f"Transcript length: {len(transcript)} characters")
            
            logger.info(f"Generating report title with LLM")
            # Generate concise report title (max 5 words)
            report_title = await self.generate_report_title(
                db=db,
                chatbot_config_id=db_conversation.chatbot_config_id,
                transcript=transcript,
            )
            logger.info(f"Report title generated: {report_title}")
            
            logger.info(f"Generating report text with LLM")
            # Generate high-level summary report
            report_text = await self.generate_report_text(
                db=db,
                chatbot_config_id=db_conversation.chatbot_config_id,
                transcript=transcript,
            )
            logger.info(f"Report text generated: {len(report_text)} characters")

            logger.info(f"Rendering PDF")
            pdf_buffer = await asyncio.to_thread(
                self.render_pdf, report_text, db_conversation, [], report_title,
            )
            logger.info(f"PDF rendered: {len(pdf_buffer.getvalue())} bytes")
            
            # Create a safe filename from the conversation title
            base_title = db_conversation.title or f"conversation-{conversation_id}"
            safe_title = self._sanitize_filename(base_title)
            filename = f"{safe_title}-report.pdf"
            
            logger.info(f"Report generation complete: {filename}")
            return filename, pdf_buffer
            
        except Exception as e:
            logger.error(f"Error in generate_report_pdf_for_conversation: {type(e).__name__}: {e}", exc_info=True)
            raise


conversation_report_service = ConversationReportService()

