# java/: reusable app functions for the UCanAccess reference engine

Put a `.java` source here only for an Access function that more than one engagement can reuse
(for example a VBA library routine that many applications share). Engagement-specific app
functions never come here: they are rewritten as correlated subqueries in
`<target>/goldens/reference/functions/<Function>.json` (docs/contracts.md#reference-catalog).

Rules:

- One public class per file, methods `public static`, each annotated with
  `@net.ucanaccess.ext.FunctionType(functionName = "...", argumentTypes = {...}, returnType = ...)`
  (`net.ucanaccess.converters.TypesMap.AccessType` values).
- Only the JDK and the UCanAccess jar are on the compile classpath.
- Write the logic from the rules spec in your own words; never copy VBA.

`toolkit.reference.bridge.start_jvm()` compiles every source here with `$JAVA_HOME/bin/javac` into
a jar in a new directory under the process `TMPDIR` at each JVM start, adds it to the classpath,
registers the annotated classes on every UCanAccess connection (`addFunctions`) and removes the
directory at exit. Nothing is written under the toolkit root, which is read-only inside mp-data's
jail and in a pinned release. `reference sql` keeps the calls of functions defined here instead of
marking their units unsupported.

There are no sources yet: the Northwind demo's app functions are all subquery rewrites.
