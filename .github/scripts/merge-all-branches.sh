#!/bin/bash

# Script to merge all branches (except main) into iv-test-consolidation

TARGET_BRANCH="iv-test-consolidation"
MAIN_BRANCH="main"

# Check if we're on the target branch
echo "Checking out $TARGET_BRANCH..."
git checkout $TARGET_BRANCH

# Array of branches to merge (excluding main)
BRANCHES_TO_MERGE=(
    "feature/backend-integration"
    "feature/db-schema-updates"
    "feature/frontend-updates"
    "feature/project-menu"
    "check-main-css"
    "investigate-error-123"
    "gap-analysis"
    "test"
)

# Merge each branch
for branch in "${BRANCHES_TO_MERGE[@]}"; do
    echo "Merging $branch into $TARGET_BRANCH..."
    git merge $branch -m "Merge $branch into $TARGET_BRANCH"
    
    if [ $? -ne 0 ]; then
        echo "Merge conflict detected with $branch!"
        echo "Please resolve conflicts and commit manually."
        exit 1
    fi
done

echo "All branches merged successfully into $TARGET_BRANCH"
