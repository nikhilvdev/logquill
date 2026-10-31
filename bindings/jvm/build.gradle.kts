plugins {
    `java-library`
    `maven-publish`
}

group = "io.github.nikhilvdev" // the Maven group ID; the Java package stays io.logquill (docs/release-setup.md, section 3)
version = "3.0.0-rc.3"

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
    withSourcesJar()
    withJavadocJar()
}

// the native library, when a build places it here (scripts/pack-smoke.sh, CI): JNA loads it from the jar by <os>-<arch>/
tasks.jar {
    from(layout.projectDirectory.dir("native"))
}

publishing {
    publications {
        create<MavenPublication>("maven") {
            from(components["java"])
            pom {
                name.set("LogQuill")
                description.set("Structured, agent-aware logging for the JVM on a Rust core.")
                url.set("https://github.com/nikhilvdev/logquill")
                licenses {
                    license {
                        name.set("MIT License")
                        url.set("https://opensource.org/licenses/MIT")
                    }
                }
                developers { developer { name.set("Nikhil Arora") } }
                scm { url.set("https://github.com/nikhilvdev/logquill") }
            }
        }
    }
    repositories {
        // a local repository for the pack gate; a real release publishes to Maven Central instead (docs/publishing.md, section 8)
        maven {
            name = "local"
            url = uri(layout.buildDirectory.dir("repo"))
        }
    }
}

repositories {
    mavenCentral()
}

dependencies {
    // the C ABI of the Rust core (crates/logquill-ffi), loaded at run time
    api("net.java.dev.jna:jna:5.17.0")
    // meta and records as JSON
    implementation("com.fasterxml.jackson.core:jackson-databind:2.19.0")
    implementation("com.fasterxml.jackson.datatype:jackson-datatype-jsr310:2.19.0")

    testImplementation(platform("org.junit:junit-bom:5.12.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.withType<JavaCompile> {
    options.encoding = "UTF-8"
    options.compilerArgs.addAll(listOf("-Xlint:all", "-Werror"))
}

tasks.test {
    useJUnitPlatform()
    // the native library built by `cargo build -p logquill-ffi --release` (a packaged jar carries it under darwin-aarch64/ etc.)
    systemProperty("jna.library.path", rootProject.file("../../target/release").absolutePath)
    testLogging { events("failed", "skipped"); showExceptions = true; exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
}

// per-call cost of the JNA layer: `cargo build -p logquill-ffi --release && gradle bench`
tasks.register<JavaExec>("bench") {
    group = "verification"
    description = "Times a log call, a filtered call and a helper call through JNA."
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("examples.Bench")
    systemProperty("jna.library.path", rootProject.file("../../target/release").absolutePath)
}
