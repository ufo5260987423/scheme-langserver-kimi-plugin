(library (test)
  (export factorial greet)
  (import (rnrs))

  (define (factorial n)
    (if (= n 0)
        1
        (* n (factorial (- n 1)))))

  (define (greet name)
    (string-append "Hello, " name))
)
