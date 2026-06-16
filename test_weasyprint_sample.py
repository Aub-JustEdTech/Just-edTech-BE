#!/usr/bin/env python3
"""
Sample script to test WeasyPrint PDF generation.
Based on official WeasyPrint documentation.
"""

from weasyprint import HTML, CSS
from datetime import datetime


def generate_sample_pdf():
    """Generate a sample PDF using WeasyPrint following official documentation."""
    
    # HTML content with professional formatting
    html_content = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            @page {
                size: letter;
                margin: 0.75in;
            }
            body {
                font-family: "Helvetica", "Arial", sans-serif;
                font-size: 10pt;
                color: #333;
                line-height: 1.5;
            }
            h1 {
                font-size: 18pt;
                color: #1a1a1a;
                margin: 0 0 20px 0;
                padding-bottom: 15px;
                border-bottom: 1px solid #b3b3b3;
                font-weight: bold;
            }
            h2 {
                font-size: 13pt;
                color: #2c5aa0;
                margin: 20px 0 10px 0;
                font-weight: bold;
            }
            p {
                margin: 0 0 12px 0;
                text-align: justify;
            }
            ul {
                margin: 8px 0;
                padding-left: 20px;
            }
            li {
                margin: 6px 0;
            }
            strong {
                font-weight: bold;
                color: #1a1a1a;
            }
            .metadata {
                color: #666;
                font-size: 9pt;
                margin-bottom: 20px;
            }
        </style>
    </head>
    <body>
        <h1>Sample Conversation Report</h1>
        
        <div class="metadata">
            Generated: ''' + datetime.now().strftime("%B %d, %Y at %I:%M %p") + '''
        </div>
        
        <h2>EXECUTIVE SUMMARY</h2>
        <p>
            This is a sample PDF report generated using <strong>WeasyPrint</strong>, 
            a Python library that converts HTML to PDF. This document demonstrates 
            proper formatting, styling, and structure for professional reports.
        </p>
        
        <h2>KEY TOPICS DISCUSSED</h2>
        <ul>
            <li><strong>PDF Generation</strong>: Using WeasyPrint to create professional documents</li>
            <li><strong>HTML/CSS Styling</strong>: Applying modern design principles to reports</li>
            <li><strong>Document Structure</strong>: Organizing content with clear sections and hierarchy</li>
            <li><strong>Typography</strong>: Selecting appropriate fonts and sizes for readability</li>
        </ul>
        
        <h2>MAIN INSIGHTS &amp; FINDINGS</h2>
        <ul>
            <li>WeasyPrint supports <strong>full CSS3</strong> styling capabilities</li>
            <li>The library handles <strong>page breaks</strong> and margins automatically</li>
            <li>HTML entities like &amp;, &lt;, and &gt; are properly escaped</li>
            <li>Custom fonts and advanced layouts are fully supported</li>
        </ul>
        
        <h2>RECOMMENDATIONS &amp; NEXT STEPS</h2>
        <ul>
            <li>Upgrade WeasyPrint to version 63.0+ to avoid compatibility issues</li>
            <li>Test PDF generation with various content types and lengths</li>
            <li>Implement error handling for edge cases</li>
            <li>Consider adding custom branding and logos to reports</li>
        </ul>
        
        <h2>TECHNICAL SPECIFICATIONS</h2>
        <p>
            This PDF was generated using the following approach:
        </p>
        <ul>
            <li><strong>Page Size</strong>: US Letter (8.5" × 11")</li>
            <li><strong>Margins</strong>: 0.75 inches on all sides</li>
            <li><strong>Font Family</strong>: Helvetica, Arial (sans-serif)</li>
            <li><strong>Base Font Size</strong>: 10pt with 1.5 line height</li>
        </ul>
        
        <h2>CONCLUSION</h2>
        <p>
            WeasyPrint provides a robust solution for generating PDF documents from HTML content. 
            By following the official documentation and using proper HTML/CSS structure, 
            you can create professional, well-formatted reports suitable for business use.
        </p>
    </body>
    </html>
    '''
    
    # Generate PDF from HTML string
    print("Generating PDF from HTML string...")
    pdf_output = '/tmp/weasyprint_sample.pdf'
    
    try:
        HTML(string=html_content).write_pdf(pdf_output)
        print(f"✅ PDF generated successfully: {pdf_output}")
        
        # Also demonstrate generating to bytes (for in-memory usage)
        pdf_bytes = HTML(string=html_content).write_pdf()
        print(f"✅ PDF generated to bytes: {len(pdf_bytes)} bytes")
        
        return pdf_output
        
    except Exception as e:
        print(f"❌ Error generating PDF: {type(e).__name__}: {e}")
        raise


def generate_simple_pdf():
    """Generate a minimal PDF to verify basic functionality."""
    
    html_content = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
    </head>
    <body>
        <h1>Hello from WeasyPrint!</h1>
        <p>This is a simple test document.</p>
        <p>If you can read this, WeasyPrint is working correctly.</p>
    </body>
    </html>
    '''
    
    print("\nGenerating simple test PDF...")
    pdf_output = '/tmp/weasyprint_simple.pdf'
    
    try:
        HTML(string=html_content).write_pdf(pdf_output)
        print(f"✅ Simple PDF generated: {pdf_output}")
        return pdf_output
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    print("=" * 60)
    print("WeasyPrint PDF Generation Test")
    print("=" * 60)
    
    # Test 1: Simple PDF
    try:
        generate_simple_pdf()
    except Exception as e:
        print(f"\n⚠️  Simple PDF test failed: {e}")
    
    # Test 2: Full sample report
    try:
        generate_sample_pdf()
    except Exception as e:
        print(f"\n⚠️  Sample report test failed: {e}")
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)
