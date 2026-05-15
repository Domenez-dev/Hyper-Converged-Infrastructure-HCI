import os
import re

tex_files = [
    'chapitre/chapitre1.tex',
    'chapitre/chapitre2.tex',
    'chapitre/chapitre3.tex',
    'chapitre/chapitre4.tex',
    'introduction.tex'
]

def clean_colors(text):
    # We will repeatedly replace the simplest pattern: {\color{something} \section{something}}
    # Since section titles don't have nested braces in MOST cases, we can try this:
    # Pattern to find {\color{bleu} \section{...}} where ... has no braces
    pattern = re.compile(r'\{\\color\{[^}]+\}\s*\\(chapter|section|subsection|subsubsection)\{([^{}]*)\}\s*\}')
    
    # We loop replacing until no more matches
    while True:
        new_text = pattern.sub(r'\\\1{\2}', text)
        if new_text == text:
            break
        text = new_text
        
    # What if there are nested braces? e.g. \section{Title with \textit{text}}
    # We can do a simpler replace: 
    # replace "{\color{bleu} \section" with "\section"
    # replace "{\color{bleuu} \subsection" with "\subsection"
    # etc...
    # But we'd leave a trailing '}' which breaks compilation!
    
    # Better approach: 
    # Just find all occurrences of `{\color{` followed by `\chapter`, `\section`, `\subsection`, `\subsubsection`
    # and use a stack to find the matching closing brace of the OUTER `{`
    
    i = 0
    while i < len(text):
        # find "{\color{"
        # We need to make sure we don't do infinite loop
        match = re.search(r'\{\\color\{[^}]+\}\s*\\(chapter|section|subsection|subsubsection)\s*\{', text[i:])
        if not match:
            break
        
        start_idx = i + match.start()
        
        # count braces to find the matching closing brace for text[start_idx]
        brace_count = 0
        end_idx = -1
        for j in range(start_idx, len(text)):
            if text[j] == '{':
                brace_count += 1
            elif text[j] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_idx = j
                    break
                    
        if end_idx != -1:
            cmd_type = match.group(1) # chapter, section etc
            inner_start = start_idx + match.end() - 1 # this is the '{' of \section{
            # inner_content is everything from inner_start to end_idx-1
            # wait, the original string is something like:
            # {\color{bleu} \section{Title}}
            # start_idx is 0
            # end_idx is len-1
            # We want to keep `\section` + `{` + inner + `}`
            # Actually, `inner_start` is the `{` of `\section{`.
            # So `text[inner_start:end_idx]` is `{Title}`
            # And we need to prefix it with `\section`.
            # But wait, what if it's `{\color{bleu} \section{Title} \label{...}}`?
            # Then the inner content is `{Title} \label{...}`
            # If we just remove `{\color{bleu} ` from the start, and the final `}` from the end, we get:
            # `\section{Title} \label{...}`
            
            # Let's extract the part from `start_idx+1` to `end_idx`
            inside_outer_braces = text[start_idx+1:end_idx]
            # Now remove the `\color{...}` part at the beginning
            inside_outer_braces = re.sub(r'^\\color\{[^}]+\}\s*', '', inside_outer_braces)
            
            # Now reconstruct the string
            text = text[:start_idx] + inside_outer_braces + text[end_idx+1:]
            
            # Do NOT increment i, so we rescan from the same place just in case
        else:
            # Malformed, skip over this match
            i += match.end()
            
    return text

for fpath in tex_files:
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = clean_colors(content)
        if new_content != content:
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("Fixed", fpath)

