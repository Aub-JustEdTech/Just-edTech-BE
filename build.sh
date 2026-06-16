#!/bin/bash
# Fast Docker build script with optimizations

set -e

echo "🚀 Building optimized Docker image with fast build features..."

# Set BuildKit for faster builds
export DOCKER_BUILDKIT=1

# Build with optimizations
docker build \
    --progress=plain \
    --build-arg BUILDKIT_INLINE_CACHE=1 \
    --tag just-edtech:latest \
    --tag just-edtech:fast \
    .

echo "✅ Build completed successfully!"
echo "📊 Checking image size..."
docker images just-edtech:latest

echo ""
echo "🎯 Build optimizations applied:"
echo "  ✅ BuildKit enabled for parallel builds"
echo "  ✅ Cache mounts for apt and pip"
echo "  ✅ Poetry export to requirements.txt"
echo "  ✅ Optimized layer caching"
echo "  ✅ Minimal context transfer"
echo ""
echo "🚀 To run: docker run -p 8000:8000 just-edtech:latest"
