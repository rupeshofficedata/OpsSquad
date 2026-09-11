# Real docker.build/docker.tag target for the build agent — same role
# terraform-demo plays for terraform.plan/apply: a real, safe, fast,
# self-contained image to actually build, no external registry needed.
FROM alpine:3.20
COPY hello.txt /hello.txt
CMD ["cat", "/hello.txt"]
