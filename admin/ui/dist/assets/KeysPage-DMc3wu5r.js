import{a1 as Sa,a2 as Ta,a3 as Ht,a4 as Pa,a5 as ct,a6 as Ra,x as za,a7 as Aa,F as Ia,a8 as $a,J as La,a9 as Ea,y as p,z as w,D as O,B as q,E as Ka,aa as At,ab as Dt,ac as It,L as Oa,O as ua,ad as Ba,M as Na,ae as Wa,af as Ma,ag as Vt,V as Fa,U as le,ah as rt,T as ot,h as ee,P as ja,Y as Ua,H as Gt,N as _e,ai as qa,W as Ha,A as Da,e as Va,a0 as Ga,_ as Xa}from"./index-CvQbP0Lt.js";import{u as Ya,B as J,E as $t,a as Ja}from"./FormField.vue_vue_type_style_index_0_scoped_ab743373_lang-CVi-aVGg.js";import{B as st,I as Qa}from"./IconBtn-lgrWYDaX.js";import{C as Xt}from"./Card-DsrAoukY.js";import{S as it}from"./Section-A48m7wkD.js";import"./ModelIcon.vue_vue_type_style_index_0_scoped_65c277c4_lang-BIOlYNh_.js";import{C as Yt}from"./Chip-CGXYZc8N.js";import{C as lt}from"./Copyable-DYdMOKOc.js";import{S as Za}from"./Segmented-DB6vkllj.js";import{K as en}from"./KV-c1gQCB1f.js";import{C as Ce}from"./CodeBlock-HqEWhdt5.js";import{D as Jt,N as Lt}from"./DataTable-DnpZInOV.js";import{F as tn}from"./FilterBar-BDbqSYpG.js";import{M as Qt}from"./Meter-DbrHPRDR.js";import{R as an}from"./RowActions-qMOPRMyi.js";import{x as Ve,A as v,f as h,i as da,K as nn,F as Ne,d as S,w as De,l as ca,H as rn,z as on,L as sn,J as ln,M as un,n as Et,y as dn,B as ue,p as cn,Z,W as f,V as m,X as i,S as L,v as N,_ as y,a1 as D,a0 as ut,U as re,$ as we}from"./useTheme-DhwxWvxW.js";import{F as fn}from"./FormDrawer-C_h-0uoh.js";import{F as de}from"./FormField-BBhvvmdV.js";import{D as dt}from"./DetailDrawer-BJxCbhgX.js";import{F as We,f as fa,h as Se,m as pn,e as Kt,a as Oe,d as ge}from"./format-CymB7ohD.js";import{u as vn}from"./useConfirm-CyDUK9Gp.js";import{u as bn}from"./useTableQuery-Dpu7thO7.js";import{R as mn}from"./refresh-cw-DrHJYjkw.js";import{P as Zt}from"./plus-BR3zFcTo.js";import{A as gn}from"./Add-DqH_S4yv.js";import{N as Be}from"./Select-MCDzO3Iy.js";import{N as ea}from"./Skeleton-DJPRwY09.js";import"./Spin-Dlkmp-wi.js";import"./feedback-CTd3riYA.js";import"./media-CZozcLMH.js";import"./Forward-B6WvahXl.js";import"./ellipsis-Di4Y3TdK.js";const yn=Ht(".v-x-scroll",{overflow:"auto",scrollbarWidth:"none"},[Ht("&::-webkit-scrollbar",{width:0,height:0})]),hn=Ve({name:"XScroll",props:{disabled:Boolean,onScroll:Function},setup(){const e=h(null);function n(o){!(o.currentTarget.offsetWidth<o.currentTarget.scrollWidth)||o.deltaY===0||(o.currentTarget.scrollLeft+=o.deltaY+o.deltaX,o.preventDefault())}const r=Sa();return yn.mount({id:"vueuc/x-scroll",head:!0,anchorMetaName:Ta,ssr:r}),Object.assign({selfRef:e,handleWheel:n},{scrollTo(...o){var c;(c=e.value)===null||c===void 0||c.scrollTo(...o)}})},render(){return v("div",{ref:"selfRef",onScroll:this.onScroll,onWheel:this.disabled?void 0:this.handleWheel,class:"v-x-scroll"},this.$slots)}});var xn=/\s/;function kn(e){for(var n=e.length;n--&&xn.test(e.charAt(n)););return n}var _n=/^\s+/;function Cn(e){return e&&e.slice(0,kn(e)+1).replace(_n,"")}var ta=NaN,wn=/^[-+]0x[0-9a-f]+$/i,Sn=/^0b[01]+$/i,Tn=/^0o[0-7]+$/i,Pn=parseInt;function aa(e){if(typeof e=="number")return e;if(Pa(e))return ta;if(ct(e)){var n=typeof e.valueOf=="function"?e.valueOf():e;e=ct(n)?n+"":n}if(typeof e!="string")return e===0?e:+e;e=Cn(e);var r=Sn.test(e);return r||Tn.test(e)?Pn(e.slice(2),r?2:8):wn.test(e)?ta:+e}var Ot=function(){return Ra.Date.now()},Rn="Expected a function",zn=Math.max,An=Math.min;function In(e,n,r){var d,o,c,g,k,x,P=0,z=!1,I=!1,j=!0;if(typeof e!="function")throw new TypeError(Rn);n=aa(n)||0,ct(r)&&(z=!!r.leading,I="maxWait"in r,c=I?zn(aa(r.maxWait)||0,n):c,j="trailing"in r?!!r.trailing:j);function W(_){var F=d,G=o;return d=o=void 0,P=_,g=e.apply(G,F),g}function B(_){return P=_,k=setTimeout(M,n),z?W(_):g}function K(_){var F=_-x,G=_-P,ce=n-F;return I?An(ce,c-G):ce}function U(_){var F=_-x,G=_-P;return x===void 0||F>=n||F<0||I&&G>=c}function M(){var _=Ot();if(U(_))return E(_);k=setTimeout(M,K(_))}function E(_){return k=void 0,j&&d?W(_):(d=o=void 0,g)}function te(){k!==void 0&&clearTimeout(k),P=0,d=x=o=k=void 0}function V(){return k===void 0?g:E(Ot())}function T(){var _=Ot(),F=U(_);if(d=arguments,o=this,x=_,F){if(k===void 0)return B(x);if(I)return clearTimeout(k),k=setTimeout(M,n),W(x)}return k===void 0&&(k=setTimeout(M,n)),g}return T.cancel=te,T.flush=V,T}var $n="Expected a function";function Ln(e,n,r){var d=!0,o=!0;if(typeof e!="function")throw new TypeError($n);return ct(r)&&(d="leading"in r?!!r.leading:d,o="trailing"in r?!!r.trailing:o),In(e,n,{leading:d,maxWait:n,trailing:o})}const jt=za("n-tabs"),pa={tab:[String,Number,Object,Function],name:{type:[String,Number],required:!0},disabled:Boolean,displayDirective:{type:String,default:"if"},closable:{type:Boolean,default:void 0},tabProps:Object,label:[String,Number,Object,Function]},He=Ve({__TAB_PANE__:!0,name:"TabPane",alias:["TabPanel"],props:pa,slots:Object,setup(e){const n=da(jt,null);return n||Aa("tab-pane","`n-tab-pane` must be placed inside `n-tabs`."),{style:n.paneStyleRef,class:n.paneClassRef,mergedClsPrefix:n.mergedClsPrefixRef}},render(){return v("div",{class:[`${this.mergedClsPrefix}-tab-pane`,this.class],style:this.style},this.$slots)}}),En=Object.assign({internalLeftPadded:Boolean,internalAddable:Boolean,internalCreatedByPane:Boolean},Ea(pa,["displayDirective"])),Mt=Ve({__TAB__:!0,inheritAttrs:!1,name:"Tab",props:En,setup(e){const{mergedClsPrefixRef:n,valueRef:r,typeRef:d,closableRef:o,tabStyleRef:c,addTabStyleRef:g,tabClassRef:k,addTabClassRef:x,tabChangeIdRef:P,onBeforeLeaveRef:z,triggerRef:I,handleAdd:j,activateTab:W,handleClose:B}=da(jt);return{trigger:I,mergedClosable:S(()=>{if(e.internalAddable)return!1;const{closable:K}=e;return K===void 0?o.value:K}),style:c,addStyle:g,tabClass:k,addTabClass:x,clsPrefix:n,value:r,type:d,handleClose(K){K.stopPropagation(),!e.disabled&&B(e.name)},activateTab(){if(e.disabled)return;if(e.internalAddable){j();return}const{name:K}=e,U=++P.id;if(K!==r.value){const{value:M}=z;M?Promise.resolve(M(e.name,r.value)).then(E=>{E&&P.id===U&&W(K)}):W(K)}}}},render(){const{internalAddable:e,clsPrefix:n,name:r,disabled:d,label:o,tab:c,value:g,mergedClosable:k,trigger:x,$slots:{default:P}}=this,z=o??c;return v("div",{class:`${n}-tabs-tab-wrapper`},this.internalLeftPadded?v("div",{class:`${n}-tabs-tab-pad`}):null,v("div",Object.assign({key:r,"data-name":r,"data-disabled":d?!0:void 0},nn({class:[`${n}-tabs-tab`,g===r&&`${n}-tabs-tab--active`,d&&`${n}-tabs-tab--disabled`,k&&`${n}-tabs-tab--closable`,e&&`${n}-tabs-tab--addable`,e?this.addTabClass:this.tabClass],onClick:x==="click"?this.activateTab:void 0,onMouseenter:x==="hover"?this.activateTab:void 0,style:e?this.addStyle:this.style},this.internalCreatedByPane?this.tabProps||{}:this.$attrs)),v("span",{class:`${n}-tabs-tab__label`},e?v(Ne,null,v("div",{class:`${n}-tabs-tab__height-placeholder`}," "),v(Ia,{clsPrefix:n},{default:()=>v(gn,null)})):P?P():typeof z=="object"?z:$a(z??r)),k&&this.type==="card"?v(La,{clsPrefix:n,class:`${n}-tabs-tab__close`,onClick:this.handleClose,disabled:d}):null))}}),Kn=p("tabs",`
 box-sizing: border-box;
 width: 100%;
 display: flex;
 flex-direction: column;
 transition:
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
`,[w("segment-type",[p("tabs-rail",[O("&.transition-disabled",[p("tabs-capsule",`
 transition: none;
 `)])])]),w("top",[p("tab-pane",`
 padding: var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left);
 `)]),w("left",[p("tab-pane",`
 padding: var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left) var(--n-pane-padding-top);
 `)]),w("left, right",`
 flex-direction: row;
 `,[p("tabs-bar",`
 width: 2px;
 right: 0;
 transition:
 top .2s var(--n-bezier),
 max-height .2s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `),p("tabs-tab",`
 padding: var(--n-tab-padding-vertical); 
 `)]),w("right",`
 flex-direction: row-reverse;
 `,[p("tab-pane",`
 padding: var(--n-pane-padding-left) var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom);
 `),p("tabs-bar",`
 left: 0;
 `)]),w("bottom",`
 flex-direction: column-reverse;
 justify-content: flex-end;
 `,[p("tab-pane",`
 padding: var(--n-pane-padding-bottom) var(--n-pane-padding-right) var(--n-pane-padding-top) var(--n-pane-padding-left);
 `),p("tabs-bar",`
 top: 0;
 `)]),p("tabs-rail",`
 position: relative;
 padding: 3px;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 background-color: var(--n-color-segment);
 transition: background-color .3s var(--n-bezier);
 display: flex;
 align-items: center;
 `,[p("tabs-capsule",`
 border-radius: var(--n-tab-border-radius);
 position: absolute;
 pointer-events: none;
 background-color: var(--n-tab-color-segment);
 box-shadow: 0 1px 3px 0 rgba(0, 0, 0, .08);
 transition: transform 0.3s var(--n-bezier);
 `),p("tabs-tab-wrapper",`
 flex-basis: 0;
 flex-grow: 1;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[p("tabs-tab",`
 overflow: hidden;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[w("active",`
 font-weight: var(--n-font-weight-strong);
 color: var(--n-tab-text-color-active);
 `),O("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])])]),w("flex",[p("tabs-nav",`
 width: 100%;
 position: relative;
 `,[p("tabs-wrapper",`
 width: 100%;
 `,[p("tabs-tab",`
 margin-right: 0;
 `)])])]),p("tabs-nav",`
 box-sizing: border-box;
 line-height: 1.5;
 display: flex;
 transition: border-color .3s var(--n-bezier);
 `,[q("prefix, suffix",`
 display: flex;
 align-items: center;
 `),q("prefix","padding-right: 16px;"),q("suffix","padding-left: 16px;")]),w("top, bottom",[O(">",[p("tabs-nav",[p("tabs-nav-scroll-wrapper",[O("&::before",`
 top: 0;
 bottom: 0;
 left: 0;
 width: 20px;
 `),O("&::after",`
 top: 0;
 bottom: 0;
 right: 0;
 width: 20px;
 `),w("shadow-start",[O("&::before",`
 box-shadow: inset 10px 0 8px -8px rgba(0, 0, 0, .12);
 `)]),w("shadow-end",[O("&::after",`
 box-shadow: inset -10px 0 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),w("left, right",[p("tabs-nav-scroll-content",`
 flex-direction: column;
 `),O(">",[p("tabs-nav",[p("tabs-nav-scroll-wrapper",[O("&::before",`
 top: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),O("&::after",`
 bottom: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),w("shadow-start",[O("&::before",`
 box-shadow: inset 0 10px 8px -8px rgba(0, 0, 0, .12);
 `)]),w("shadow-end",[O("&::after",`
 box-shadow: inset 0 -10px 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),p("tabs-nav-scroll-wrapper",`
 flex: 1;
 position: relative;
 overflow: hidden;
 `,[p("tabs-nav-y-scroll",`
 height: 100%;
 width: 100%;
 overflow-y: auto; 
 scrollbar-width: none;
 `,[O("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb",`
 width: 0;
 height: 0;
 display: none;
 `)]),O("&::before, &::after",`
 transition: box-shadow .3s var(--n-bezier);
 pointer-events: none;
 content: "";
 position: absolute;
 z-index: 1;
 `)]),p("tabs-nav-scroll-content",`
 display: flex;
 position: relative;
 min-width: 100%;
 min-height: 100%;
 width: fit-content;
 box-sizing: border-box;
 `),p("tabs-wrapper",`
 display: inline-flex;
 flex-wrap: nowrap;
 position: relative;
 `),p("tabs-tab-wrapper",`
 display: flex;
 flex-wrap: nowrap;
 flex-shrink: 0;
 flex-grow: 0;
 `),p("tabs-tab",`
 cursor: pointer;
 white-space: nowrap;
 flex-wrap: nowrap;
 display: inline-flex;
 align-items: center;
 color: var(--n-tab-text-color);
 font-size: var(--n-tab-font-size);
 background-clip: padding-box;
 padding: var(--n-tab-padding);
 transition:
 box-shadow .3s var(--n-bezier),
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 `,[w("disabled",{cursor:"not-allowed"}),q("close",`
 margin-left: 6px;
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `),q("label",`
 display: flex;
 align-items: center;
 z-index: 1;
 `)]),p("tabs-bar",`
 position: absolute;
 bottom: 0;
 height: 2px;
 border-radius: 1px;
 background-color: var(--n-bar-color);
 transition:
 left .2s var(--n-bezier),
 max-width .2s var(--n-bezier),
 opacity .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `,[O("&.transition-disabled",`
 transition: none;
 `),w("disabled",`
 background-color: var(--n-tab-text-color-disabled)
 `)]),p("tabs-pane-wrapper",`
 position: relative;
 overflow: hidden;
 transition: max-height .2s var(--n-bezier);
 `),p("tab-pane",`
 color: var(--n-pane-text-color);
 width: 100%;
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 opacity .2s var(--n-bezier);
 left: 0;
 right: 0;
 top: 0;
 `,[O("&.next-transition-leave-active, &.prev-transition-leave-active, &.next-transition-enter-active, &.prev-transition-enter-active",`
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 transform .2s var(--n-bezier),
 opacity .2s var(--n-bezier);
 `),O("&.next-transition-leave-active, &.prev-transition-leave-active",`
 position: absolute;
 `),O("&.next-transition-enter-from, &.prev-transition-leave-to",`
 transform: translateX(32px);
 opacity: 0;
 `),O("&.next-transition-leave-to, &.prev-transition-enter-from",`
 transform: translateX(-32px);
 opacity: 0;
 `),O("&.next-transition-leave-from, &.next-transition-enter-to, &.prev-transition-leave-from, &.prev-transition-enter-to",`
 transform: translateX(0);
 opacity: 1;
 `)]),p("tabs-tab-pad",`
 box-sizing: border-box;
 width: var(--n-tab-gap);
 flex-grow: 0;
 flex-shrink: 0;
 `),w("line-type, bar-type",[p("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 box-sizing: border-box;
 vertical-align: bottom;
 `,[O("&:hover",{color:"var(--n-tab-text-color-hover)"}),w("active",`
 color: var(--n-tab-text-color-active);
 font-weight: var(--n-tab-font-weight-active);
 `),w("disabled",{color:"var(--n-tab-text-color-disabled)"})])]),p("tabs-nav",[w("line-type",[w("top",[q("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 bottom: -1px;
 `)]),w("left",[q("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 right: -1px;
 `)]),w("right",[q("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 left: -1px;
 `)]),w("bottom",[q("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 top: -1px;
 `)]),q("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-nav-scroll-content",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-bar",`
 border-radius: 0;
 `)]),w("card-type",[q("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-pad",`
 flex-grow: 1;
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-tab-pad",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 border: 1px solid var(--n-tab-border-color);
 background-color: var(--n-tab-color);
 box-sizing: border-box;
 position: relative;
 vertical-align: bottom;
 display: flex;
 justify-content: space-between;
 font-size: var(--n-tab-font-size);
 color: var(--n-tab-text-color);
 `,[w("addable",`
 padding-left: 8px;
 padding-right: 8px;
 font-size: 16px;
 justify-content: center;
 `,[q("height-placeholder",`
 width: 0;
 font-size: var(--n-tab-font-size);
 `),Ka("disabled",[O("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])]),w("closable","padding-right: 8px;"),w("active",`
 background-color: #0000;
 font-weight: var(--n-tab-font-weight-active);
 color: var(--n-tab-text-color-active);
 `),w("disabled","color: var(--n-tab-text-color-disabled);")])]),w("left, right",`
 flex-direction: column; 
 `,[q("prefix, suffix",`
 padding: var(--n-tab-padding-vertical);
 `),p("tabs-wrapper",`
 flex-direction: column;
 `),p("tabs-tab-wrapper",`
 flex-direction: column;
 `,[p("tabs-tab-pad",`
 height: var(--n-tab-gap-vertical);
 width: 100%;
 `)])]),w("top",[w("card-type",[p("tabs-scroll-padding","border-bottom: 1px solid var(--n-tab-border-color);"),q("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-top-right-radius: var(--n-tab-border-radius);
 `,[w("active",`
 border-bottom: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `)])]),w("left",[w("card-type",[p("tabs-scroll-padding","border-right: 1px solid var(--n-tab-border-color);"),q("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-bottom-left-radius: var(--n-tab-border-radius);
 `,[w("active",`
 border-right: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `)])]),w("right",[w("card-type",[p("tabs-scroll-padding","border-left: 1px solid var(--n-tab-border-color);"),q("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-right-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[w("active",`
 border-left: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `)])]),w("bottom",[w("card-type",[p("tabs-scroll-padding","border-top: 1px solid var(--n-tab-border-color);"),q("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-bottom-left-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[w("active",`
 border-top: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `)])])])]),Bt=Ln,On=Object.assign(Object.assign({},ua.props),{value:[String,Number],defaultValue:[String,Number],trigger:{type:String,default:"click"},type:{type:String,default:"bar"},closable:Boolean,justifyContent:String,size:String,placement:{type:String,default:"top"},tabStyle:[String,Object],tabClass:String,addTabStyle:[String,Object],addTabClass:String,barWidth:Number,paneClass:String,paneStyle:[String,Object],paneWrapperClass:String,paneWrapperStyle:[String,Object],addable:[Boolean,Object],tabsPadding:{type:Number,default:0},animated:Boolean,onBeforeLeave:Function,onAdd:Function,"onUpdate:value":[Function,Array],onUpdateValue:[Function,Array],onClose:[Function,Array],labelSize:String,activeName:[String,Number],onActiveNameChange:[Function,Array]}),Bn=Ve({name:"Tabs",props:On,slots:Object,setup(e,{slots:n}){var r,d,o,c;const{mergedClsPrefixRef:g,inlineThemeDisabled:k,mergedComponentPropsRef:x}=Oa(e),P=ua("Tabs","-tabs",Kn,Ba,e,g),z=h(null),I=h(null),j=h(null),W=h(null),B=h(null),K=h(null),U=h(!0),M=h(!0),E=Vt(e,["labelSize","size"]),te=S(()=>{var u,l;if(E.value)return E.value;const b=(l=(u=x?.value)===null||u===void 0?void 0:u.Tabs)===null||l===void 0?void 0:l.size;return b||"medium"}),V=Vt(e,["activeName","value"]),T=h((d=(r=V.value)!==null&&r!==void 0?r:e.defaultValue)!==null&&d!==void 0?d:n.default?(c=(o=At(n.default())[0])===null||o===void 0?void 0:o.props)===null||c===void 0?void 0:c.name:null),_=Na(V,T),F={id:0},G=S(()=>{if(!(!e.justifyContent||e.type==="card"))return{display:"flex",justifyContent:e.justifyContent}});De(_,()=>{F.id=0,ae(),Xe()});function ce(){var u;const{value:l}=_;return l===null?null:(u=z.value)===null||u===void 0?void 0:u.querySelector(`[data-name="${l}"]`)}function ft(u){if(e.type==="card")return;const{value:l}=I;if(!l)return;const b=l.style.opacity==="0";if(u){const C=`${g.value}-tabs-bar--disabled`,{barWidth:$,placement:Y}=e;if(u.dataset.disabled==="true"?l.classList.add(C):l.classList.remove(C),["top","bottom"].includes(Y)){if(Ge(["top","maxHeight","height"]),typeof $=="number"&&u.offsetWidth>=$){const H=Math.floor((u.offsetWidth-$)/2)+u.offsetLeft;l.style.left=`${H}px`,l.style.maxWidth=`${$}px`}else l.style.left=`${u.offsetLeft}px`,l.style.maxWidth=`${u.offsetWidth}px`;l.style.width="8192px",b&&(l.style.transition="none"),l.offsetWidth,b&&(l.style.transition="",l.style.opacity="1")}else{if(Ge(["left","maxWidth","width"]),typeof $=="number"&&u.offsetHeight>=$){const H=Math.floor((u.offsetHeight-$)/2)+u.offsetTop;l.style.top=`${H}px`,l.style.maxHeight=`${$}px`}else l.style.top=`${u.offsetTop}px`,l.style.maxHeight=`${u.offsetHeight}px`;l.style.height="8192px",b&&(l.style.transition="none"),l.offsetHeight,b&&(l.style.transition="",l.style.opacity="1")}}}function fe(){if(e.type==="card")return;const{value:u}=I;u&&(u.style.opacity="0")}function Ge(u){const{value:l}=I;if(l)for(const b of u)l.style[b]=""}function ae(){if(e.type==="card")return;const u=ce();u?ft(u):fe()}function Xe(){var u;const l=(u=B.value)===null||u===void 0?void 0:u.$el;if(!l)return;const b=ce();if(!b)return;const{scrollLeft:C,offsetWidth:$}=l,{offsetLeft:Y,offsetWidth:H}=b;C>Y?l.scrollTo({top:0,left:Y,behavior:"smooth"}):Y+H>C+$&&l.scrollTo({top:0,left:Y+H-$,behavior:"smooth"})}const Te=h(null);let Pe=0,X=null;function Q(u){const l=Te.value;if(l){Pe=u.getBoundingClientRect().height;const b=`${Pe}px`,C=()=>{l.style.height=b,l.style.maxHeight=b};X?(C(),X(),X=null):X=C}}function R(u){const l=Te.value;if(l){const b=u.getBoundingClientRect().height,C=()=>{document.body.offsetHeight,l.style.maxHeight=`${b}px`,l.style.height=`${Math.max(Pe,b)}px`};X?(X(),X=null,C()):X=C}}function oe(){const u=Te.value;if(u){u.style.maxHeight="",u.style.height="";const{paneWrapperStyle:l}=e;if(typeof l=="string")u.style.cssText=l;else if(l){const{maxHeight:b,height:C}=l;b!==void 0&&(u.style.maxHeight=b),C!==void 0&&(u.style.height=C)}}}const Re={value:[]},Me=h("next");function ze(u){const l=_.value;let b="next";for(const C of Re.value){if(C===l)break;if(C===u){b="prev";break}}Me.value=b,ye(u)}function ye(u){const{onActiveNameChange:l,onUpdateValue:b,"onUpdate:value":C}=e;l&&ot(l,u),b&&ot(b,u),C&&ot(C,u),T.value=u}function Fe(u){const{onClose:l}=e;l&&ot(l,u)}function Ae(){const{value:u}=I;if(!u)return;const l="transition-disabled";u.classList.add(l),ae(),u.classList.remove(l)}const se=h(null);function Ie({transitionDisabled:u}){const l=z.value;if(!l)return;u&&l.classList.add("transition-disabled");const b=ce();b&&se.value&&(se.value.style.width=`${b.offsetWidth}px`,se.value.style.height=`${b.offsetHeight}px`,se.value.style.transform=`translateX(${b.offsetLeft-Fa(getComputedStyle(l).paddingLeft)}px)`,u&&se.value.offsetWidth),u&&l.classList.remove("transition-disabled")}De([_],()=>{e.type==="segment"&&Et(()=>{Ie({transitionDisabled:!1})})}),ca(()=>{e.type==="segment"&&Ie({transitionDisabled:!0})});let Ye=0;function pt(u){var l;if(u.contentRect.width===0&&u.contentRect.height===0||Ye===u.contentRect.width)return;Ye=u.contentRect.width;const{type:b}=e;if((b==="line"||b==="bar")&&Ae(),b!=="segment"){const{placement:C}=e;me((C==="top"||C==="bottom"?(l=B.value)===null||l===void 0?void 0:l.$el:K.value)||null)}}const vt=Bt(pt,64);De([()=>e.justifyContent,()=>e.size],()=>{Et(()=>{const{type:u}=e;(u==="line"||u==="bar")&&Ae()})});const be=h(!1);function bt(u){var l;const{target:b,contentRect:{width:C,height:$}}=u,Y=b.parentElement.parentElement.offsetWidth,H=b.parentElement.parentElement.offsetHeight,{placement:pe}=e;if(!be.value)pe==="top"||pe==="bottom"?Y<C&&(be.value=!0):H<$&&(be.value=!0);else{const{value:xe}=W;if(!xe)return;pe==="top"||pe==="bottom"?Y-C>xe.$el.offsetWidth&&(be.value=!1):H-$>xe.$el.offsetHeight&&(be.value=!1)}me(((l=B.value)===null||l===void 0?void 0:l.$el)||null)}const $e=Bt(bt,64);function je(){const{onAdd:u}=e;u&&u(),Et(()=>{const l=ce(),{value:b}=B;!l||!b||b.scrollTo({left:l.offsetLeft,top:0,behavior:"smooth"})})}function me(u){if(!u)return;const{placement:l}=e;if(l==="top"||l==="bottom"){const{scrollLeft:b,scrollWidth:C,offsetWidth:$}=u;U.value=b<=0,M.value=b+$>=C}else{const{scrollTop:b,scrollHeight:C,offsetHeight:$}=u;U.value=b<=0,M.value=b+$>=C}}const Le=Bt(u=>{me(u.target)},64);dn(jt,{triggerRef:ue(e,"trigger"),tabStyleRef:ue(e,"tabStyle"),tabClassRef:ue(e,"tabClass"),addTabStyleRef:ue(e,"addTabStyle"),addTabClassRef:ue(e,"addTabClass"),paneClassRef:ue(e,"paneClass"),paneStyleRef:ue(e,"paneStyle"),mergedClsPrefixRef:g,typeRef:ue(e,"type"),closableRef:ue(e,"closable"),valueRef:_,tabChangeIdRef:F,onBeforeLeaveRef:ue(e,"onBeforeLeave"),activateTab:ze,handleClose:Fe,handleAdd:je}),Wa(()=>{ae(),Xe()}),rn(()=>{const{value:u}=j;if(!u)return;const{value:l}=g,b=`${l}-tabs-nav-scroll-wrapper--shadow-start`,C=`${l}-tabs-nav-scroll-wrapper--shadow-end`;U.value?u.classList.remove(b):u.classList.add(b),M.value?u.classList.remove(C):u.classList.add(C)});const Ee={syncBarPosition:()=>{ae()}},Ue=()=>{Ie({transitionDisabled:!0})},he=S(()=>{const{value:u}=te,{type:l}=e,b={card:"Card",bar:"Bar",line:"Line",segment:"Segment"}[l],C=`${u}${b}`,{self:{barColor:$,closeIconColor:Y,closeIconColorHover:H,closeIconColorPressed:pe,tabColor:xe,tabBorderColor:Je,paneTextColor:mt,tabFontWeight:gt,tabBorderRadius:ie,tabFontWeightActive:Qe,colorSegment:Ze,fontWeightStrong:et,tabColorSegment:ke,closeSize:yt,closeIconSize:ht,closeColorHover:xt,closeColorPressed:tt,closeBorderRadius:qe,[le("panePadding",u)]:Ke,[le("tabPadding",C)]:kt,[le("tabPaddingVertical",C)]:at,[le("tabGap",C)]:_t,[le("tabGap",`${C}Vertical`)]:nt,[le("tabTextColor",l)]:Ct,[le("tabTextColorActive",l)]:wt,[le("tabTextColorHover",l)]:St,[le("tabTextColorDisabled",l)]:Tt,[le("tabFontSize",u)]:Pt},common:{cubicBezierEaseInOut:Rt}}=P.value;return{"--n-bezier":Rt,"--n-color-segment":Ze,"--n-bar-color":$,"--n-tab-font-size":Pt,"--n-tab-text-color":Ct,"--n-tab-text-color-active":wt,"--n-tab-text-color-disabled":Tt,"--n-tab-text-color-hover":St,"--n-pane-text-color":mt,"--n-tab-border-color":Je,"--n-tab-border-radius":ie,"--n-close-size":yt,"--n-close-icon-size":ht,"--n-close-color-hover":xt,"--n-close-color-pressed":tt,"--n-close-border-radius":qe,"--n-close-icon-color":Y,"--n-close-icon-color-hover":H,"--n-close-icon-color-pressed":pe,"--n-tab-color":xe,"--n-tab-font-weight":gt,"--n-tab-font-weight-active":Qe,"--n-tab-padding":kt,"--n-tab-padding-vertical":at,"--n-tab-gap":_t,"--n-tab-gap-vertical":nt,"--n-pane-padding-left":rt(Ke,"left"),"--n-pane-padding-right":rt(Ke,"right"),"--n-pane-padding-top":rt(Ke,"top"),"--n-pane-padding-bottom":rt(Ke,"bottom"),"--n-font-weight-strong":et,"--n-tab-color-segment":ke}}),ne=k?Ma("tabs",S(()=>`${te.value[0]}${e.type[0]}`),he,e):void 0;return Object.assign({mergedClsPrefix:g,mergedValue:_,renderedNames:new Set,segmentCapsuleElRef:se,tabsPaneWrapperRef:Te,tabsElRef:z,barElRef:I,addTabInstRef:W,xScrollInstRef:B,scrollWrapperElRef:j,addTabFixed:be,tabWrapperStyle:G,handleNavResize:vt,mergedSize:te,handleScroll:Le,handleTabsResize:$e,cssVars:k?void 0:he,themeClass:ne?.themeClass,animationDirection:Me,renderNameListRef:Re,yScrollElRef:K,handleSegmentResize:Ue,onAnimationBeforeLeave:Q,onAnimationEnter:R,onAnimationAfterEnter:oe,onRender:ne?.onRender},Ee)},render(){const{mergedClsPrefix:e,type:n,placement:r,addTabFixed:d,addable:o,mergedSize:c,renderNameListRef:g,onRender:k,paneWrapperClass:x,paneWrapperStyle:P,$slots:{default:z,prefix:I,suffix:j}}=this;k?.();const W=z?At(z()).filter(T=>T.type.__TAB_PANE__===!0):[],B=z?At(z()).filter(T=>T.type.__TAB__===!0):[],K=!B.length,U=n==="card",M=n==="segment",E=!U&&!M&&this.justifyContent;g.value=[];const te=()=>{const T=v("div",{style:this.tabWrapperStyle,class:`${e}-tabs-wrapper`},E?null:v("div",{class:`${e}-tabs-scroll-padding`,style:r==="top"||r==="bottom"?{width:`${this.tabsPadding}px`}:{height:`${this.tabsPadding}px`}}),K?W.map((_,F)=>(g.value.push(_.props.name),Nt(v(Mt,Object.assign({},_.props,{internalCreatedByPane:!0,internalLeftPadded:F!==0&&(!E||E==="center"||E==="start"||E==="end")}),_.children?{default:_.children.tab}:void 0)))):B.map((_,F)=>(g.value.push(_.props.name),Nt(F!==0&&!E?oa(_):_))),!d&&o&&U?ra(o,(K?W.length:B.length)!==0):null,E?null:v("div",{class:`${e}-tabs-scroll-padding`,style:{width:`${this.tabsPadding}px`}}));return v("div",{ref:"tabsElRef",class:`${e}-tabs-nav-scroll-content`},U&&o?v(It,{onResize:this.handleTabsResize},{default:()=>T}):T,U?v("div",{class:`${e}-tabs-pad`}):null,U?null:v("div",{ref:"barElRef",class:`${e}-tabs-bar`}))},V=M?"top":r;return v("div",{class:[`${e}-tabs`,this.themeClass,`${e}-tabs--${n}-type`,`${e}-tabs--${c}-size`,E&&`${e}-tabs--flex`,`${e}-tabs--${V}`],style:this.cssVars},v("div",{class:[`${e}-tabs-nav--${n}-type`,`${e}-tabs-nav--${V}`,`${e}-tabs-nav`]},Dt(I,T=>T&&v("div",{class:`${e}-tabs-nav__prefix`},T)),M?v(It,{onResize:this.handleSegmentResize},{default:()=>v("div",{class:`${e}-tabs-rail`,ref:"tabsElRef"},v("div",{class:`${e}-tabs-capsule`,ref:"segmentCapsuleElRef"},v("div",{class:`${e}-tabs-wrapper`},v("div",{class:`${e}-tabs-tab`}))),K?W.map((T,_)=>(g.value.push(T.props.name),v(Mt,Object.assign({},T.props,{internalCreatedByPane:!0,internalLeftPadded:_!==0}),T.children?{default:T.children.tab}:void 0))):B.map((T,_)=>(g.value.push(T.props.name),_===0?T:oa(T))))}):v(It,{onResize:this.handleNavResize},{default:()=>v("div",{class:`${e}-tabs-nav-scroll-wrapper`,ref:"scrollWrapperElRef"},["top","bottom"].includes(V)?v(hn,{ref:"xScrollInstRef",onScroll:this.handleScroll},{default:te}):v("div",{class:`${e}-tabs-nav-y-scroll`,onScroll:this.handleScroll,ref:"yScrollElRef"},te()))}),d&&o&&U?ra(o,!0):null,Dt(j,T=>T&&v("div",{class:`${e}-tabs-nav__suffix`},T))),K&&(this.animated&&(V==="top"||V==="bottom")?v("div",{ref:"tabsPaneWrapperRef",style:P,class:[`${e}-tabs-pane-wrapper`,x]},na(W,this.mergedValue,this.renderedNames,this.onAnimationBeforeLeave,this.onAnimationEnter,this.onAnimationAfterEnter,this.animationDirection)):na(W,this.mergedValue,this.renderedNames)))}});function na(e,n,r,d,o,c,g){const k=[];return e.forEach(x=>{const{name:P,displayDirective:z,"display-directive":I}=x.props,j=B=>z===B||I===B,W=n===P;if(x.key!==void 0&&(x.key=P),W||j("show")||j("show:lazy")&&r.has(P)){r.has(P)||r.add(P);const B=!j("if");k.push(B?on(x,[[sn,W]]):x)}}),g?v(ln,{name:`${g}-transition`,onBeforeLeave:d,onEnter:o,onAfterEnter:c},{default:()=>k}):k}function ra(e,n){return v(Mt,{ref:"addTabInstRef",key:"__addable",name:"__addable",internalCreatedByPane:!0,internalAddable:!0,internalLeftPadded:n,disabled:typeof e=="object"&&e.disabled})}function oa(e){const n=un(e);return n.props?n.props.internalLeftPadded=!0:n.props={internalLeftPadded:!0},n}function Nt(e){return Array.isArray(e.dynamicProps)?e.dynamicProps.includes("internalLeftPadded")||e.dynamicProps.push("internalLeftPadded"):e.dynamicProps=["internalLeftPadded"],e}function Nn(e){return ee("GET","/api/keys",{signal:e})}function Wn(e){return ee("POST","/api/keys",{body:e})}function Mn(e){return ee("GET","/api/keys/"+e+"/view")}function Fn(e){return ee("GET","/api/keys/"+e+"/usage")}function jn(e,n={}){return ee("GET","/api/keys/"+e+"/ips",{query:{days:n.days??1,sort:n.sort??"requests",direction:n.direction??"desc",limit:n.limit??100,offset:n.offset??0}})}function Wt(e,n){return ee("PATCH","/api/keys/"+e,{body:n})}function Un(e){return ee("DELETE","/api/keys/"+e)}function qn(){return ee("GET","/api/credits/selection-policy")}function Hn(){return ee("GET","/api/app/network")}function Dn(){return ee("GET","/api/models/aggregate")}function Vn(){return ee("GET","/api/groups")}function Gn(e={}){return ee("GET","/api/accounts",{query:{all_platforms:1,page:e.page??1,page_size:e.pageSize??50,search:e.search||""}})}function Xn(){return ee("GET","/api/platforms")}function Yn(e){return(Math.round(e*1e3)/10).toFixed(1)+"%"}function Jn(e){const n=!!e.unlimited,r=Number(e.credit_limit||0),d=Number(e.credit_used||0),o=n||r<=0?0:Math.min(1,Math.max(0,d/r)),c=n?"ok":o>=.9?"danger":o>=.6?"warn":"ok",g=e.ip_summary||{days:1,unique_ips:0,requests:0,active_ips:0,active_requests:0},k=e.max_concurrent??null,x=e.rate_per_minute??null,P=Array.isArray(e.account_ids)?e.account_ids.map(Number).filter(I=>I>0):[],z=e.masked||(e.key_prefix?pn(e.key_prefix):We);return{id:e.id,name:e.name||"未命名",masked:z,keyPrefix:e.key_prefix||"",limitText:n?"∞":Se(r),usedText:Se(d),usageRatio:o,usageTone:c,usageText:n?"不限额度":Yn(o),unlimited:n,concurrencyText:k===0?"不限":k==null?"全局":String(k),rateText:x===0?"不限频率":x==null?"频率随全局":x+" 次/分",ipText:(Number(g.unique_ips)||0)+" 个 / 24h",ipBadges:(g.top_ips||[]).slice(0,3).map(I=>({ip:I.ip,text:I.ip+" · "+(Number(I.active_requests)||0)+" 并发 · "+(Number(I.requests)||0)+" 次"})),groupLabel:e.group_name||"全部模型",statusLabel:e.status==="active"?"正常":"已停用",statusTone:e.status==="active"?"ok":"neutral",accountLabel:P.length?"独占 "+P.length+" 个账号":e.account_id?"独占账号："+(e.account_name||"#"+e.account_id):"公共账号池",expireText:We,note:e.note||"",platform:e.platform||"",aggregate:!!e.aggregate,groupId:Number(e.group_id||0),createdAt:e.created_at||"",status:e.status,creditLimit:r,creditUsed:d,maxConcurrent:k,ratePerMinute:x,accountIds:P,accountId:e.account_id,accountName:e.account_name||"",ipSummary:g}}function Qn(e){return Array.isArray(e)?e.map(Jn):[]}function Zn(e,n){const r=String(n.q||"").trim().toLowerCase(),d=String(n.group||""),o=String(n.status||"");return e.filter(c=>!(r&&![c.name,c.note,c.accountName,c.platform].join(" ").toLowerCase().includes(r)||d==="none"&&c.groupId!==0||d&&d!=="none"&&String(c.groupId)!==d||o&&c.status!==o))}function er(e,n,r){if(!n||r!=="asc"&&r!=="desc")return e;const d=r==="asc"?1:-1,o=c=>n==="name"?0:n==="limit"?c.unlimited?Number.MAX_SAFE_INTEGER:c.creditLimit:n==="used"?c.creditUsed:n==="concurrency"?c.maxConcurrent==null?-1:c.maxConcurrent:n==="created"&&Date.parse(c.createdAt||"")||0;return[...e].sort((c,g)=>n==="name"?c.name.localeCompare(g.name,"zh-CN")*d:(o(c)-o(g))*d)}function tr(e,n,r){const d=Math.max(1,Math.floor(r||20)),o=e.length,c=Math.max(1,Math.ceil(o/d)),g=Math.min(Math.max(1,Math.floor(n||1)),c);return{items:e.slice((g-1)*d,g*d),total:o,page:g,pageCount:c}}function sa(e){return{total:e.length,active:e.filter(n=>n.status==="active").length,revoked:e.filter(n=>n.status!=="active").length,unlimited:e.filter(n=>n.unlimited).length}}function ar(e){return{status:e==="active"?"revoked":"active"}}function Ft(e,n){const r=String(e||""),d=String(n||"");return!r||!d||r.toLowerCase().endsWith("-"+d.toLowerCase())?r:r+"-"+d}function nr(e,n=12){const r=(e?.items||[]).filter(o=>(o.platforms||[]).some(c=>c.enabled!==!1)),d=r.slice(0,Math.max(0,n)).map(o=>{const c=(o.platforms||[]).filter(x=>x.enabled!==!1),g=c[0],k=(c.length?c.map(x=>{const P=x.client_id||Ft(x.model_id,x.platform),z=x.multiplier==null?We:Number(x.multiplier)===0?"FREE":"×"+x.multiplier;return(x.platform_label||x.platform)+" · "+P+" · "+z}).join(`
`):"暂无平台明细")+`
点击复制调用名（带平台后缀）`;return{key:o.key||String(o.name),name:o.name,platformCount:c.length||Number(o.platform_count||0),copyValue:g?g.client_id||Ft(g.model_id,g.platform):"",title:k}});return{chips:d,total:r.length,rest:Math.max(0,r.length-d.length)}}function rr(e,n){const r=String(n||"").trim().toLowerCase();return(e?.items||[]).filter(d=>(d.platforms||[]).some(o=>o.enabled!==!1)).map(d=>{const o=(d.platforms||[]).filter(c=>c.enabled!==!1);return{key:d.key||String(d.name),name:d.name,platformCount:o.length,rows:o.map(c=>({platform:c.platform,platformLabel:c.platform_label||c.platform,callId:c.client_id||Ft(c.model_id,c.platform),upstreamId:c.model_id,multiplierText:c.multiplier==null?We:Number(c.multiplier)===0?"FREE":"×"+c.multiplier}))}}).filter(d=>!r||d.name.toLowerCase().includes(r)?!0:d.rows.some(o=>o.callId.toLowerCase().includes(r)||o.upstreamId.toLowerCase().includes(r)))}function or(e,n){const r=new Map;n.origin&&r.set(n.origin,"当前访问 · "+(n.origin.replace(/^https?:\/\//,"")||"当前域名"));const d=["localhost","127.0.0.1"].includes(n.hostname),o=n.port||(d&&e?.port?String(e.port):""),c=o?":"+o:"";r.set("http://127.0.0.1"+c,"本机 · 127.0.0.1"+c);for(const g of e?.lan_ips||[])r.set("http://"+g+c,"局域网 · "+g+c);return[...r].map(([g,k])=>({value:g,label:k}))}function sr(e,n){const r=(e||"").replace(/\/+$/,"")+"/v1",d=(e||"").replace(/\/+$/,""),o=n||"auto";return{openai:["curl "+r+"/chat/completions \\",'  -H "Authorization: Bearer <你的 Key>" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"`+o+`","messages":[{"role":"user","content":"你好"}]}'`].join(`
`),claude:["curl "+d+"/v1/messages \\",'  -H "x-api-key: <你的 Key>" \\','  -H "anthropic-version: 2023-06-01" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":1024,"messages":[{"role":"user","content":"你好"}]}'`].join(`
`),audioCurl:['curl "'+r+'/audio/transcriptions" \\','  -H "Authorization: Bearer <你的 Key>" \\','  -F "file=@recording.webm" \\','  -F "model=workbuddy-asr" \\','  -F "response_format=json"'].join(`
`),audioPython:["from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")','with open("recording.webm", "rb") as audio:','    result = client.audio.transcriptions.create(model="workbuddy-asr", file=audio)',"print(result.text)"].join(`
`),imagesPython:["from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")','result = client.images.generate(model="hunyuan-image-alpha", prompt="白色背景上的蓝色圆形", n=1)',"print(result.data[0].url)",'with open("input.png", "rb") as image:','    edited = client.images.edit(model="hunyuan-image-alpha", image=image, prompt="把圆形改成绿色")',"print(edited.data[0].url)"].join(`
`),videoCurl:["# 1) 创建任务：立即返回 queued，不阻塞等待生成",'curl "'+r+'/videos" \\','  -H "Authorization: Bearer <你的 Key>" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"kling-video-v3","prompt":"一只猫在弹钢琴","seconds":"4","size":"1280x720"}'`,"","# 2) 轮询（用上一步返回的 id），直到 status=completed",'curl "'+r+'/videos/<视频 ID>" -H "Authorization: Bearer <你的 Key>"',"","# 3) 下载 MP4",'curl "'+r+'/videos/<视频 ID>/content" -H "Authorization: Bearer <你的 Key>" -o output.mp4'].join(`
`),videoPython:["import time","from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")',"",'video = client.videos.create(model="kling-video-v3", prompt="一只猫在弹钢琴", seconds="4", size="1280x720")',"print(video.id, video.status)          # queued","",'while video.status not in ("completed", "failed"):',"    time.sleep(5)","    video = client.videos.retrieve(video.id)","",'if video.status == "completed":',"    content = client.videos.download_content(video.id)",'    content.write_to_file("output.mp4")'].join(`
`)}}function ia(e,n){const r=String(e||"").trim();if(!r)return;const d=Number(r);if(!Number.isFinite(d))return n+"必须是数字";if(d<0)return n+"不能小于 0";if(!Number.isInteger(d))return n+"必须是整数"}function ir(e,n){const r={};if(n&&!String(e.name||"").trim()&&(r.name="名称不能为空"),!e.unlimited){const d=String(e.creditLimit||"").trim();if(d){const o=Number(d);Number.isFinite(o)?o<0&&(r.creditLimit="额度不能小于 0"):r.creditLimit="额度必须是数字"}}return r.maxConcurrent=ia(e.maxConcurrent,"并发上限"),r.ratePerMinute=ia(e.ratePerMinute,"每分钟请求数"),r}function lr(e){return Object.values(e).some(Boolean)}function ur(e){const n=d=>{const o=String(d||"").trim();return o===""?null:Number(o)},r=String(e.creditLimit||"").trim();return{name:String(e.name||"").trim(),note:String(e.note||""),group_id:Number(e.groupId||0),unlimited:e.unlimited||r==="",credit_limit:r===""?null:Number(r),max_concurrent:n(e.maxConcurrent),rate_per_minute:n(e.ratePerMinute),aggregate:!!e.aggregate,platform:String(e.platform||"").toLowerCase(),account_ids:e.accountIds.slice()}}function dr(e){const n=!!e.unlimited,r=e.usage_percent,d=n||r==null?0:Math.min(1,Math.max(0,r/100));return{limitText:n?"∞（无限制）":Se(e.credit_limit),usedText:Se(e.credit_used),remainingText:n||e.credit_remaining==null?"∞":Se(e.credit_remaining),percentText:r==null?"N/A":r.toFixed(1)+"%",ratio:d,tone:n?"ok":d>=.9?"danger":d>=.6?"warn":"ok",unlimited:n,createdText:e.created_at?fa(e.created_at):We}}function la(e){return{totalIps:Number(e.total_ips||0),requests:Number(e.total_requests||0),activeIps:Number(e.active_ips||0),activeRequests:Number(e.active_requests||0),rows:(e.items||[]).map(n=>({ip:n.ip,requests:Number(n.requests||0),failed:Number(n.failed||0),credits:Number(n.credits||0),tokens:Number(n.tokens||0),lastSeen:n.last_seen?fa(n.last_seen):We,models:(n.models||[]).map(r=>r.model).filter(Boolean).join("、"),active:Number(n.active_requests||0),blocked:!!n.blocked}))}}const cr={class:"keys-page"},fr={class:"keys-policy"},pr={class:"keys-share__grid"},vr={class:"keys-share__row"},br={class:"keys-share__row"},mr={class:"keys-share__row"},gr={class:"keys-share__row"},yr={class:"keys-share__row keys-share__row--models"},hr={class:"keys-share__models"},xr={class:"keys-muted"},kr={key:0,class:"keys-chip-count"},_r={key:1,class:"keys-muted"},Cr={class:"keys-filter"},wr={class:"keys-body"},Sr={key:1,class:"keys-skeleton","aria-busy":"true","aria-label":"Key 加载中"},Tr={class:"keys-form"},Pr={class:"keys-form"},Rr={class:"keys-form__inline"},zr={class:"keys-form"},Ar={class:"keys-modal__footer"},Ir={key:1,class:"keys-skeleton"},$r={class:"keys-usage-grid"},Lr={class:"keys-usage-cell"},Er={class:"tnum"},Kr={class:"keys-usage-cell"},Or={class:"tnum"},Br={class:"keys-usage-cell"},Nr={class:"tnum"},Wr={class:"keys-usage-cell"},Mr={class:"tnum"},Fr={class:"keys-hint"},jr={class:"keys-ipbar"},Ur={key:0,class:"keys-muted"},qr={class:"keys-model__head"},Hr={class:"keys-muted"},Dr={class:"keys-model__rows"},Vr={class:"keys-muted"},Gr={class:"keys-muted tnum"},Xr="对外共享的调用凭证：每个 Key 单独计量、限额与封禁，共享出去不影响后台登录。OpenAI 与 Anthropic 两套协议共用同一批 Key、同一套配额与用量记账，只是 base_url 约定不同：OpenAI SDK 填含 /v1 的那个，Claude Code / Anthropic SDK 填不含 /v1 的那个（SDK 会自己拼 /v1/messages，填错会变成 /v1/v1/messages）。",Yr="OpenAI 与 Anthropic 两套协议共用同一批 Key、同一套配额与用量记账，只是 base_url 约定不同：OpenAI SDK 填 OpenAI 地址（含 /v1），Claude Code / Anthropic SDK 填 Claude 地址（不含 /v1，SDK 会自己拼 /v1/messages，填错会变成 /v1/v1/messages）。Claude 端的 claude-sonnet-4-5 这类模型名由服务端自动按档次映射到白名单里的模型，无需手动改。",Jr=Ve({__name:"KeysPage",setup(e){const n=Ua(),r=Ya(),d=vn(),o=bn("keys",{pageSize:20,filters:{q:"",group:"",status:""}}),c=h(null),g=h(!1),k=h(null);let x=null;const P=S(()=>Qn(c.value)),z=S(()=>Zn(P.value,o.state.filters)),I=S(()=>er(z.value,o.state.sortBy,o.state.sortOrder)),j=S(()=>tr(I.value,o.state.page,o.state.pageSize)),W=S(()=>sa(P.value)),B=S(()=>g.value&&c.value===null),K=S(()=>!!(o.state.filters.q||o.state.filters.group||o.state.filters.status)),U=S(()=>{const a=sa(z.value);return K.value?"筛选出 "+a.total+" / 共 "+W.value.total+" 个 Key · 正常 "+a.active:"共 "+a.total+" 个 Key · 正常 "+a.active+" · 已停用 "+a.revoked+" · 不限额 "+a.unlimited}),M=S(()=>K.value?"没有符合条件的 Key。清空筛选后可看到全部 Key。":"还没有 Key。创建 Key 后即可对外调用网关。"),E=h(null),te=h(null),V=h(null),T=h(!1),_={origin:typeof window<"u"?window.location.origin:"",hostname:typeof window<"u"?window.location.hostname:"",port:typeof window<"u"?window.location.port:""},F=S(()=>or(te.value,_)),G=h(_.origin);De(F,a=>{a.length&&!a.some(t=>t.value===G.value)&&(G.value=a[0].value)});const ce=S(()=>(G.value||"").replace(/\/+$/,"")+"/v1"),ft=S(()=>(G.value||"").replace(/\/+$/,"")),fe=S(()=>nr(V.value,12)),Ge=S(()=>fe.value.chips[0]?.copyValue||""),ae=S(()=>sr(G.value,Ge.value)),Xe=S(()=>(E.value?.modes||[]).find(t=>t.value===E.value?.mode)?.label||"账号调度策略"),Te=S(()=>((E.value?.modes||[]).find(t=>t.value===E.value?.mode)?.description||"决定网关挑哪一个账号承接请求。")+"（在「积分到期」页修改）");function Pe(){return{name:"",note:"",groupId:0,unlimited:!0,creditLimit:"",maxConcurrent:"",ratePerMinute:"",aggregate:!0,platform:"",accountIds:[],resetUsed:!1}}const X=h(!1),Q=h(null),R=h(Pe()),oe=h({}),Re=h(!1),Me=h(""),ze=h(!1),ye=h([]),Fe=h([]),Ae=h([]),se=h(!1);let Ie=!1;const Ye=S(()=>[{label:"不限制（全部可用模型）",value:0},...ye.value.map(a=>({label:a.name+(a.effective_count!=null?"（"+a.effective_count+" 个模型）":""),value:a.id}))]),pt=S(()=>[{label:"不限制（全部平台）",value:""},...Fe.value.map(a=>({label:a.label,value:a.storage}))]),vt=S(()=>Ae.value.map(a=>({label:(a.name||a.uid||"账号")+" #"+a.id+(a.status&&a.status!=="active"?" · 已禁用":"")+(a.balance_remain!=null?" · "+Se(a.balance_remain)+" 积分":""),value:a.id}))),be=S(()=>Q.value?"编辑 API Key":"创建 API Key"),bt=S(()=>Q.value?"保存":"创建"),$e=h(!1),je=h(!1),me=h(""),Le=h(null),Ee=h(!1),Ue=h(!1),he=h(""),ne=h("1"),u=h(""),l=h([]),b=h(la({total_ips:0,total_requests:0,active_ips:0,active_requests:0,items:[]}));let C=0;const $=h(!1),Y=h(""),H=h(!1),pe=h("openai"),xe=S(()=>[{key:"ip",title:"IP 地址",mobile:"title",width:150},{key:"requests",title:"调用次数",mobile:"meta",width:100,numeric:!0,render:a=>ge(a.requests)},{key:"failed",title:"失败",mobile:"meta",width:80,numeric:!0,render:a=>ge(a.failed)},{key:"credits",title:"积分",mobile:"meta",width:90,numeric:!0,render:a=>Se(a.credits)},{key:"tokens",title:"Token",mobile:"meta",width:100,numeric:!0,render:a=>ge(a.tokens)},{key:"lastSeen",title:"最近调用",mobile:"meta",width:150},{key:"models",title:"使用模型",mobile:"subtitle",minWidth:180},{key:"active",title:"并发",mobile:"meta",width:80,numeric:!0,render:a=>ge(a.active)},{key:"blocked",title:"状态",mobile:"meta",width:90,render:a=>v(st,{tone:a.blocked?"danger":"ok"},{default:()=>a.blocked?"已封禁":"正常"})}]),Je=S(()=>rr(V.value,Y.value)),mt=S(()=>l.value);function gt(a){if(typeof a=="string")return a;try{return JSON.stringify(a,null,2)}catch{return String(a)}}async function ie(){x?.abort();const a=new AbortController;x=a,g.value=!0,k.value=null;try{const t=await Nn(a.signal);c.value=Array.isArray(t?.items)?t.items:[];const s=Math.max(1,Math.ceil(z.value.length/o.state.pageSize));o.state.page>s&&o.setPage(s)}catch(t){if(Ha(t))return;k.value={message:Kt(t),detail:t instanceof Da&&t.detail?gt(t.detail):""},r.error(Oe(c.value===null?"加载 Key":"刷新 Key",t))}finally{x===a&&(x=null,g.value=!1)}}async function Qe(){qn().then(a=>{E.value=a}).catch(()=>{E.value=null}),Hn().then(a=>{te.value=a}).catch(()=>{te.value=null}),T.value=!0;try{V.value=await Dn()}catch{V.value=null}finally{T.value=!1}}async function Ze(a){se.value=!0;try{const t=await Gn({search:a,pageSize:50});Ae.value=Array.isArray(t?.items)?t.items:t?.accounts||[]}catch{Ae.value=[]}finally{se.value=!1}}async function et(){Ie||(Ie=!0,Vn().then(a=>{ye.value=Array.isArray(a?.items)?a.items:[]}).catch(()=>{ye.value=[]}),Xn().then(a=>{const t=new Map;for(const s of a?.items||[]){const A=String(s.storage_platform||s.id||"").toLowerCase();!A||t.has(A)||t.set(A,String(s.label||A))}Fe.value=[...t].map(([s,A])=>({storage:s,label:A}))}).catch(()=>{Fe.value=[]}),await Ze(""))}const ke=h(o.state.filters.q);De(()=>o.state.filters.q,a=>{ke.value=a});const yt=[{label:"状态：全部",value:""},{label:"正常",value:"active"},{label:"已停用",value:"revoked"}],ht=S(()=>[{label:"分组：全部",value:""},{label:"未绑定分组",value:"none"},...ye.value.map(a=>({label:a.name,value:String(a.id)}))]),xt=S(()=>{const a=o.state.filters,t=[];if(a.q&&t.push({key:"q",label:"搜索",value:a.q}),a.group){const s=a.group==="none"?"未绑定分组":ye.value.find(A=>String(A.id)===a.group)?.name||a.group;t.push({key:"group",label:"分组",value:s})}return a.status&&t.push({key:"status",label:"状态",value:a.status==="active"?"正常":"已停用"}),t}),tt={name:"name",limit:"limit",used:"used",concurrency:"concurrency"};function qe(a){return o.state.sortBy!==a?!1:o.state.sortOrder==="asc"?"ascend":o.state.sortOrder==="desc"?"descend":!1}function Ke(a){if(!a||!a.order||!tt[String(a.columnKey)]){o.setSort("","");return}o.setSort(tt[String(a.columnKey)],a.order==="ascend"?"asc":"desc")}function kt(a){const t={};for(const[s,A]of Object.entries(a))t[s]=String(A??"");o.setFilters(t)}function at(a,t){o.setFilter(a,t==null?"":String(t))}function _t(a){a==="q"&&(ke.value=""),o.setFilter(a,"")}function nt(){ke.value="",o.reset()}function Ct(a){const t=a;return v("div",{class:"keys-name"},[v("span",{class:"keys-name__main",title:t.name},t.name),v("span",{class:"keys-name__sub"},t.accountLabel)])}function wt(a){const t=a;return v("div",{class:"keys-key"},[v("code",{class:"mono keys-key__text",title:t.masked},t.masked),v(Qa,{icon:Va,label:"复制完整 Key",onClick:()=>{Ut(t)}})])}function St(a){const t=a;return v("div",{class:"keys-quota"},[v("div",{class:"keys-quota__line tnum"},[v("span",{},t.usedText),v("span",{class:"keys-quota__sep"}," / "),v("span",{},t.limitText)]),t.unlimited?v("span",{class:"keys-quota__hint"},"不限额"):v(Qt,{value:t.usageRatio*100,tone:t.usageTone,height:4,ariaLabel:"额度使用率"})])}function Tt(a){const t=a;return v("div",{class:"keys-concurrency"},[v("span",{class:"tnum"},t.concurrencyText),v("span",{class:"keys-concurrency__rate"},t.rateText)])}function Pt(a){const t=a;return v("div",{class:"keys-ips"},[v("button",{type:"button",class:"keys-ips__count",title:"查看该 Key 的来源 IP 与请求次数，用于识别共享范围与异常来源",onClick:()=>{Ca(t)}},t.ipText),t.ipBadges.length?v("div",{class:"keys-ips__badges"},t.ipBadges.map(s=>v(st,{key:s.ip,tone:"brand",title:s.text},{default:()=>s.ip}))):null])}function Rt(a){const t=a;return t.groupId?v(st,{tone:"brand",title:"该 Key 仅可使用所绑定分组内的模型"},{default:()=>t.groupLabel}):v("span",{class:"keys-muted"},t.groupLabel)}function va(a){return v("span",{class:"keys-muted",title:"后端 Key 模型没有到期字段，到期时间未提供"},a.expireText)}function ba(a){const t=a;return v(st,{tone:t.statusTone,dot:!0},{default:()=>t.statusLabel})}function ma(a){const t=a;return v(an,{actions:[{key:"edit",label:"编辑"},{key:"copy",label:"复制 Key"},{key:"reset",label:"重置用量"},{key:"toggle",label:t.status==="active"?"停用":"启用"},{key:"usage",label:"查看用量"},{key:"delete",label:"删除",danger:!0}],max:2,onSelect:s=>{ka(t,s)}})}const ga=S(()=>[{key:"name",title:"名称",mobile:"title",minWidth:180,sortable:!0,sortOrder:qe("name"),render:Ct},{key:"key",title:"Key",mobile:"subtitle",minWidth:220,render:wt},{key:"quota",title:"限额 / 已用",mobile:"meta",minWidth:170,sortable:!0,sortOrder:qe("used"),render:St},{key:"concurrency",title:"并发",mobile:"meta",width:120,sortable:!0,sortOrder:qe("concurrency"),render:Tt},{key:"ips",title:"来源 IP",mobile:"meta",minWidth:170,render:Pt},{key:"group",title:"分组",mobile:"meta",width:130,render:Rt},{key:"expire",title:"到期",mobile:"meta",width:90,render:va},{key:"status",title:"状态",mobile:"meta",width:96,render:ba},{key:"actions",title:"操作",mobile:"actions",width:150,hideable:!1,fixed:"right",render:ma}]);function ya(a){const t=Array.isArray(a)?a:a===null||a===""?[]:[a];R.value.accountIds=t.map(Number).filter(s=>Number.isFinite(s)&&s>0)}async function Ut(a){try{const t=await Mn(a.id);await Ga(t.key)?r.success("已复制完整 Key"):r.error("复制失败：浏览器拒绝了剪贴板访问，请手动选中复制")}catch(t){r.error(Oe("读取 Key",t))}}function qt(){Q.value=null,R.value=Pe(),oe.value={},X.value=!0,et()}function ha(a){Q.value=a,R.value={name:a.name,note:a.note,groupId:a.groupId,unlimited:a.unlimited,creditLimit:a.unlimited?"":String(a.creditLimit),maxConcurrent:a.maxConcurrent==null?"":String(a.maxConcurrent),ratePerMinute:a.ratePerMinute==null?"":String(a.ratePerMinute),aggregate:a.aggregate,platform:a.platform,accountIds:a.accountIds.slice(),resetUsed:!1},oe.value={},X.value=!0,et()}async function xa(){if(oe.value=ir(R.value,!!Q.value),!lr(oe.value)){Re.value=!0;try{const a=ur(R.value);if(Q.value)await Wt(Q.value.id,{...a,reset_used:R.value.resetUsed}),r.success("已保存"),X.value=!1,await ie();else{const t=await Wn({...a,credit_limit:a.credit_limit??null});X.value=!1,Me.value=t.key,ze.value=!0,await ie()}}catch(a){r.error(Oe(Q.value?"保存 Key":"创建 Key",a))}finally{Re.value=!1}}}async function ka(a,t){if(t==="edit")return ha(a);if(t==="copy")return Ut(a);if(t==="usage")return _a(a);if(t==="reset"){if(!await d({title:"重置已用额度？",message:"已用积分归零，历史日志保留。该 Key 立即恢复全部额度。",okText:"重置用量"}))return;try{await Wt(a.id,{reset_used:!0}),r.success("已重置已用额度"),await ie()}catch(A){r.error(Oe("重置用量",A))}return}if(t==="toggle"){try{await Wt(a.id,ar(a.status)),r.success(a.status==="active"?"已停用":"已启用"),await ie()}catch(s){r.error(Oe(a.status==="active"?"停用 Key":"启用 Key",s))}return}if(t==="delete"){if(!await d({title:"删除该 Key？",message:"删除后该 Key 立即无法调用网关，用量记录保留。如需临时停止调用，请使用停用。",danger:!0,okText:"删除"}))return;try{await Un(a.id),r.success("已删除"),await ie()}catch(A){r.error(Oe("删除 Key",A))}}}async function _a(a){$e.value=!0,je.value=!0,me.value="",Le.value=null;try{Le.value=await Fn(a.id)}catch(t){me.value=Kt(t)}finally{je.value=!1}}async function zt(){if(C){Ue.value=!0,he.value="";try{const a=await jn(C,{days:Number(ne.value)||1});l.value=a.items||[],b.value=la(a)}catch(a){he.value=Kt(a),l.value=[]}finally{Ue.value=!1}}}async function Ca(a){C=a.id,u.value=a.name,ne.value="1",l.value=[],Ee.value=!0,await zt()}const wa=S(()=>[{label:"来源 IP",value:ge(b.value.totalIps)},{label:"调用次数",value:ge(b.value.requests)},{label:"当前活跃 IP",value:ge(b.value.activeIps)},{label:"当前并发",value:ge(b.value.activeRequests)}]),ve=S(()=>Le.value?dr(Le.value):null);return ca(()=>{ie(),Qe()}),cn(()=>{x?.abort()}),(a,t)=>(L(),Z("div",cr,[f(ja,{title:"API Keys",help:Xr},{actions:m(()=>[f(i(J),{variant:"quiet",icon:i(mn),loading:g.value,onClick:t[0]||(t[0]=s=>ie())},{default:m(()=>[...t[35]||(t[35]=[N("刷新",-1)])]),_:1},8,["icon","loading"]),y("span",fr,[f(i(J),{variant:"quiet",onClick:t[1]||(t[1]=s=>i(n).push("/credits"))},{default:m(()=>[N(D(Xe.value),1)]),_:1}),f(i(Gt),{content:Te.value,label:"账号调度策略说明"},null,8,["content"])]),f(i(J),{variant:"default",onClick:t[2]||(t[2]=s=>H.value=!0)},{default:m(()=>[...t[36]||(t[36]=[N("接入指南",-1)])]),_:1}),f(i(J),{variant:"primary",icon:i(Zt),onClick:qt},{default:m(()=>[...t[37]||(t[37]=[N("创建 Key",-1)])]),_:1},8,["icon"])]),_:1}),f(i(Xt),{class:"keys-share",title:"对接信息"},{extra:m(()=>[f(i(J),{variant:"quiet",size:"sm",loading:T.value,onClick:t[3]||(t[3]=s=>Qe())},{default:m(()=>[...t[38]||(t[38]=[N("刷新",-1)])]),_:1},8,["loading"])]),default:m(()=>[y("div",pr,[y("div",vr,[t[39]||(t[39]=y("span",{class:"keys-share__label"},"连接地址",-1)),f(i(Be),{class:"keys-share__select",value:G.value,options:F.value,"onUpdate:value":t[4]||(t[4]=s=>G.value=s)},null,8,["value","options"])]),y("div",br,[t[40]||(t[40]=y("span",{class:"keys-share__label"},[N("OpenAI"),y("em",null,"含 /v1")],-1)),f(i(lt),{value:ce.value},null,8,["value"])]),y("div",mr,[t[41]||(t[41]=y("span",{class:"keys-share__label"},[N("Claude"),y("em",null,"不含 /v1")],-1)),f(i(lt),{value:ft.value},null,8,["value"])]),y("div",gr,[t[42]||(t[42]=y("span",{class:"keys-share__label"},"鉴权",-1)),f(i(lt),{value:"Authorization: Bearer <你的 Key>　或　x-api-key: <你的 Key>"}),f(i(Gt),{content:Yr,label:"鉴权说明"})]),y("div",yr,[t[43]||(t[43]=y("span",{class:"keys-share__label"},"可用模型",-1)),y("div",hr,[y("span",xr,D(fe.value.total)+" 组",1),(L(!0),Z(Ne,null,ut(fe.value.chips,s=>(L(),re(i(Yt),{key:s.key,mono:"",copy:"",value:s.copyValue,title:s.title},{default:m(()=>[N(D(s.name),1),s.platformCount>1?(L(),Z("span",kr,D(s.platformCount),1)):we("",!0)]),_:2},1032,["value","title"]))),128)),fe.value.total?(L(),re(i(J),{key:0,variant:"quiet",size:"sm",onClick:t[5]||(t[5]=s=>$.value=!0)},{default:m(()=>[N(" 全部 "+D(fe.value.total)+" ▸ ",1)]),_:1})):we("",!0),!fe.value.total&&!T.value?(L(),Z("span",_r,"未配置模型白名单（放行全部模型）")):we("",!0)])])])]),_:1}),f(i(Xt),{flush:"",class:"keys-card"},{default:m(()=>[y("div",Cr,[f(i(tn),{chips:xt.value,summary:U.value,loading:g.value,onChange:kt,onReset:nt,onRemove:_t},{default:m(({change:s})=>[f(i(_e),{class:"keys-filter__search",value:ke.value,placeholder:"搜索名称 / 备注 / 账号",clearable:"","onUpdate:value":A=>{ke.value=A,s("q",A)}},null,8,["value","onUpdate:value"]),f(i(Be),{class:"keys-filter__select",value:i(o).state.filters.group,options:ht.value,"onUpdate:value":t[6]||(t[6]=A=>at("group",A))},null,8,["value","options"]),f(i(Be),{class:"keys-filter__select",value:i(o).state.filters.status,options:yt,"onUpdate:value":t[7]||(t[7]=A=>at("status",A))},null,8,["value"])]),_:1},8,["chips","summary","loading"])]),y("div",wr,[k.value&&c.value===null?(L(),re(i($t),{key:0,title:"加载 Key 失败："+k.value.message,detail:k.value.detail,onRetry:t[8]||(t[8]=s=>ie())},null,8,["title","detail"])):B.value?(L(),Z("div",Sr,[(L(),Z(Ne,null,ut(6,s=>f(i(ea),{key:s,height:"44px"})),64))])):(L(),re(i(Jt),{key:2,"table-id":"keys",columns:ga.value,data:j.value.items,"row-key":"id",remote:"",loading:g.value,page:i(o).state.page,"page-size":i(o).state.pageSize,"item-count":j.value.total,"page-sizes":[20,50,100],"empty-description":M.value,"onUpdate:page":i(o).setPage,"onUpdate:pageSize":i(o).setPageSize,"onUpdate:sorter":Ke},{empty:m(()=>[f(i(Ja),{description:M.value},{default:m(()=>[K.value?(L(),re(i(J),{key:0,variant:"default",size:"sm",onClick:nt},{default:m(()=>[...t[44]||(t[44]=[N("清除筛选",-1)])]),_:1})):(L(),re(i(J),{key:1,variant:"primary",size:"sm",icon:i(Zt),onClick:qt},{default:m(()=>[...t[45]||(t[45]=[N("创建 Key",-1)])]),_:1},8,["icon"]))]),_:1},8,["description"])]),_:1},8,["columns","data","loading","page","page-size","item-count","empty-description","onUpdate:page","onUpdate:pageSize"]))])]),_:1}),f(i(fn),{show:X.value,"onUpdate:show":t[20]||(t[20]=s=>X.value=s),title:be.value,size:"lg","submit-text":bt.value,"submit-loading":Re.value,onSubmit:xa},{default:m(()=>[f(i(it),{title:"基本",first:""},{default:m(()=>[y("div",Tr,[f(i(de),{label:"名称",required:!!Q.value,error:oe.value.name,hint:"创建时可留空，后端会填「未命名」。"},{default:m(()=>[f(i(_e),{value:R.value.name,"onUpdate:value":t[9]||(t[9]=s=>R.value.name=s),placeholder:"例如：给设计组的 Key",maxlength:"120",clearable:""},null,8,["value"])]),_:1},8,["required","error"]),f(i(de),{label:"模型分组",hint:"选择后该 Key 只能调用组内模型，调用组外模型返回 403。"},{default:m(()=>[f(i(Be),{value:R.value.groupId,"onUpdate:value":t[10]||(t[10]=s=>R.value.groupId=s),options:Ye.value},null,8,["value","options"])]),_:1}),f(i(de),{label:"备注"},{default:m(()=>[f(i(_e),{value:R.value.note,"onUpdate:value":t[11]||(t[11]=s=>R.value.note=s),type:"textarea",rows:2,maxlength:"255",placeholder:"可选"},null,8,["value"])]),_:1})])]),_:1}),f(i(it),{title:"额度与流量"},{default:m(()=>[y("div",Pr,[f(i(de),{label:"总额度（积分）",error:oe.value.creditLimit,hint:"勾选「不限额」时忽略；填 0 表示零额度。"},{default:m(()=>[y("div",Rr,[f(i(Lt),{checked:R.value.unlimited,"onUpdate:checked":t[12]||(t[12]=s=>R.value.unlimited=s)},{default:m(()=>[...t[46]||(t[46]=[N("不限额（∞）",-1)])]),_:1},8,["checked"]),f(i(_e),{value:R.value.creditLimit,"onUpdate:value":t[13]||(t[13]=s=>R.value.creditLimit=s),placeholder:"留空不限额；0 表示零额度",disabled:R.value.unlimited},null,8,["value","disabled"])])]),_:1},8,["error"]),f(i(de),{label:"并发上限",error:oe.value.maxConcurrent,hint:"空 = 跟随全局；0 = 不限；N = 该 Key 所有对话合计最多 N 个在途请求。"},{default:m(()=>[f(i(_e),{value:R.value.maxConcurrent,"onUpdate:value":t[14]||(t[14]=s=>R.value.maxConcurrent=s),placeholder:"空 = 全局，0 = 不限"},null,8,["value"])]),_:1},8,["error"]),f(i(de),{label:"每分钟请求数",error:oe.value.ratePerMinute,hint:"频率与并发独立；积分不限额也不会解除流量限制。"},{default:m(()=>[f(i(_e),{value:R.value.ratePerMinute,"onUpdate:value":t[15]||(t[15]=s=>R.value.ratePerMinute=s),placeholder:"空 = 全局，0 = 不限"},null,8,["value"])]),_:1},8,["error"]),Q.value?(L(),re(i(de),{key:0,label:"重置已用额度"},{default:m(()=>[f(i(Lt),{checked:R.value.resetUsed,"onUpdate:checked":t[16]||(t[16]=s=>R.value.resetUsed=s)},{default:m(()=>[N(" 勾选后把已用积分归零（当前 "+D(Q.value?.usedText)+"，不影响历史日志） ",1)]),_:1},8,["checked"])]),_:1})):we("",!0)])]),_:1}),f(i(it),{title:"模型与账号"},{default:m(()=>[y("div",zr,[f(i(de),{label:"聚合模型路由",hint:"开启后 /v1/models 返回跨平台聚合列表，可由网关挑平台转发。"},{default:m(()=>[f(i(Lt),{checked:R.value.aggregate,"onUpdate:checked":t[17]||(t[17]=s=>R.value.aggregate=s)},{default:m(()=>[...t[47]||(t[47]=[N("使用聚合模型（跨平台负载分摊）",-1)])]),_:1},8,["checked"])]),_:1}),f(i(de),{label:"独占平台",hint:"绑定后只在该平台的账号池里选号；不限制则按请求自报平台。"},{default:m(()=>[f(i(Be),{value:R.value.platform,"onUpdate:value":t[18]||(t[18]=s=>R.value.platform=s),options:pt.value},null,8,["value","options"])]),_:1}),f(i(de),{label:"独占绑定账号（多选）",hint:"不选 = 使用公共账号池；选中后只用这些账号，账号不可用时不换号。搜索走服务端。"},{default:m(()=>[f(i(Be),{value:R.value.accountIds,multiple:"",filterable:"",remote:"",clearable:"",loading:se.value,options:vt.value,placeholder:"不限制（使用公共账号池）","onUpdate:value":ya,onSearch:t[19]||(t[19]=s=>Ze(s))},null,8,["value","loading","options"])]),_:1})])]),_:1}),f(i(it),{title:"访问与有效期"},{default:m(()=>[...t[48]||(t[48]=[y("p",{class:"keys-hint"}," 来源 IP 只用于统计（表格「来源 IP」列可查看），后端没有「绑定来源 IP」字段，因此这里不提供绑定位。 ",-1),y("p",{class:"keys-hint"},"后端 Key 模型没有到期字段，因此不提供有效期设置，表格「到期」列显示 —。",-1)])]),_:1})]),_:1},8,["show","title","submit-text","submit-loading"]),f(i(qa),{show:ze.value,"onUpdate:show":t[22]||(t[22]=s=>ze.value=s),preset:"card",class:"keys-modal",title:"Key 已创建",style:{width:"520px"}},{footer:m(()=>[y("div",Ar,[f(i(J),{variant:"primary",onClick:t[21]||(t[21]=s=>ze.value=!1)},{default:m(()=>[...t[49]||(t[49]=[N("完成",-1)])]),_:1})])]),default:m(()=>[t[50]||(t[50]=y("p",{class:"keys-hint"},"可随时在列表里点「复制 Key」再次查看完整值。",-1)),f(i(lt),{value:Me.value},null,8,["value"])]),_:1},8,["show"]),f(i(dt),{show:$e.value,"onUpdate:show":t[24]||(t[24]=s=>$e.value=s),title:"Key 用量",subtitle:"按额度统计"},{footer:m(()=>[f(i(J),{variant:"default",onClick:t[23]||(t[23]=s=>$e.value=!1)},{default:m(()=>[...t[55]||(t[55]=[N("关闭",-1)])]),_:1})]),default:m(()=>[me.value?(L(),re(i($t),{key:0,title:"读取用量失败："+me.value,retry:!1,compact:""},null,8,["title"])):je.value?(L(),Z("div",Ir,[f(i(ea),{repeat:3,text:""})])):ve.value?(L(),Z(Ne,{key:2},[y("div",$r,[y("div",Lr,[t[51]||(t[51]=y("span",null,"限额",-1)),y("strong",Er,D(ve.value.limitText),1)]),y("div",Kr,[t[52]||(t[52]=y("span",null,"已用",-1)),y("strong",Or,D(ve.value.usedText),1)]),y("div",Br,[t[53]||(t[53]=y("span",null,"剩余",-1)),y("strong",Nr,D(ve.value.remainingText),1)]),y("div",Wr,[t[54]||(t[54]=y("span",null,"使用率",-1)),y("strong",Mr,D(ve.value.percentText),1)])]),ve.value.unlimited?we("",!0):(L(),re(i(Qt),{key:0,value:ve.value.ratio*100,tone:ve.value.tone,height:8,"aria-label":"额度使用率"},null,8,["value","tone"])),y("p",Fr,"创建于 "+D(ve.value.createdText),1)],64)):we("",!0)]),_:1},8,["show"]),f(i(dt),{show:Ee.value,"onUpdate:show":t[28]||(t[28]=s=>Ee.value=s),title:"来源 IP · "+u.value,subtitle:"并发为当前服务进程的在途请求"},{footer:m(()=>[f(i(J),{variant:"default",onClick:t[27]||(t[27]=s=>Ee.value=!1)},{default:m(()=>[...t[57]||(t[57]=[N("关闭",-1)])]),_:1})]),default:m(()=>[y("div",jr,[f(i(Za),{options:[{label:"24 小时",value:"1"},{label:"7 天",value:"7"},{label:"30 天",value:"30"}],"model-value":ne.value,"aria-label":"时间范围","onUpdate:modelValue":t[25]||(t[25]=s=>{ne.value=String(s),zt()})},null,8,["model-value"]),f(i(J),{variant:"quiet",size:"sm",loading:Ue.value,onClick:t[26]||(t[26]=s=>zt())},{default:m(()=>[...t[56]||(t[56]=[N("刷新",-1)])]),_:1},8,["loading"])]),f(i(en),{items:wa.value},null,8,["items"]),he.value?(L(),re(i($t),{key:0,title:"读取来源 IP 失败："+he.value,retry:!1,compact:""},null,8,["title"])):(L(),re(i(Jt),{key:1,"table-id":"keys-ips",columns:xe.value,data:mt.value,"row-key":"ip",pagination:!1,"virtual-scroll":!1,"max-height":"360px","empty-description":"所选时间内暂无调用记录"},null,8,["columns","data"])),t[58]||(t[58]=y("p",{class:"keys-hint"},"封禁 / 解封在「IP 管理」页操作；这里只读展示统计。",-1))]),_:1},8,["show","title"]),f(i(dt),{show:$.value,"onUpdate:show":t[31]||(t[31]=s=>$.value=s),title:"可用模型 · "+fe.value.total+" 组",subtitle:"点击模型名复制调用名（带平台后缀）"},{footer:m(()=>[f(i(J),{variant:"default",onClick:t[30]||(t[30]=s=>$.value=!1)},{default:m(()=>[...t[59]||(t[59]=[N("关闭",-1)])]),_:1})]),default:m(()=>[f(i(_e),{value:Y.value,"onUpdate:value":t[29]||(t[29]=s=>Y.value=s),placeholder:"搜索模型名 / 调用名 / 上游 id",clearable:""},null,8,["value"]),Je.value.length?we("",!0):(L(),Z("div",Ur,"没有匹配的模型。")),(L(!0),Z(Ne,null,ut(Je.value,s=>(L(),Z("div",{key:s.key,class:"keys-model"},[y("div",qr,[y("strong",null,D(s.name),1),y("span",Hr,D(s.platformCount)+" 平台",1)]),y("div",Dr,[(L(!0),Z(Ne,null,ut(s.rows,A=>(L(),Z("div",{key:A.callId,class:"keys-model__row"},[y("span",Vr,D(A.platformLabel),1),f(i(Yt),{mono:"",copy:"",value:A.callId,title:"上游 id："+A.upstreamId},{default:m(()=>[N(D(A.callId),1)]),_:2},1032,["value","title"]),y("span",Gr,D(A.multiplierText),1)]))),128))])]))),128))]),_:1},8,["show","title"]),f(i(dt),{show:H.value,"onUpdate:show":t[34]||(t[34]=s=>H.value=s),title:"接入指南",subtitle:"地址与模型已按当前连接地址填好"},{footer:m(()=>[f(i(J),{variant:"default",onClick:t[33]||(t[33]=s=>H.value=!1)},{default:m(()=>[...t[63]||(t[63]=[N("关闭",-1)])]),_:1})]),default:m(()=>[f(i(Bn),{value:pe.value,"onUpdate:value":t[32]||(t[32]=s=>pe.value=s),type:"line",animated:""},{default:m(()=>[f(i(He),{name:"openai",tab:"OpenAI 协议"},{default:m(()=>[f(i(Ce),{code:ae.value.openai,language:"bash"},null,8,["code"])]),_:1}),f(i(He),{name:"claude",tab:"Claude Code / Anthropic"},{default:m(()=>[f(i(Ce),{code:ae.value.claude,language:"bash"},null,8,["code"])]),_:1}),f(i(He),{name:"audio",tab:"语音转文字"},{default:m(()=>[t[60]||(t[60]=y("p",{class:"keys-hint"}," POST /v1/audio/transcriptions，使用同一 API Key：multipart/form-data 上传 file， model=workbuddy-asr；response_format 可选 json（默认）或 text。网关文件上限 25 MiB， 不支持 language / prompt / 时间戳 / 流式转写；不保存音频与转写内容。 ",-1)),f(i(Ce),{code:ae.value.audioCurl,language:"bash"},null,8,["code"]),f(i(Ce),{code:ae.value.audioPython,language:"python"},null,8,["code"])]),_:1}),f(i(He),{name:"images",tab:"AI 生图"},{default:m(()=>[t[61]||(t[61]=y("p",{class:"keys-hint"}," 文生图 POST /v1/images/generations，图生图 POST /v1/images/edits（单张 image，最大 20 MiB； 也支持 image_url / base64 image_data）。返回 url（默认）或 b64_json；暂不支持 mask、流式、多图。 ",-1)),f(i(Ce),{code:ae.value.imagesPython,language:"python"},null,8,["code"])]),_:1}),f(i(He),{name:"videos",tab:"AI 生视频（异步任务）"},{default:m(()=>[t[62]||(t[62]=y("p",{class:"keys-hint"}," 创建 POST /v1/videos 只到「上游已入队」就返回 queued；轮询 GET /v1/videos/{id}， 下载 GET /v1/videos/{id}/content。生成通常需要 1~5 分钟，按上游 usage.credit 实际积分记账。 ",-1)),f(i(Ce),{code:ae.value.videoCurl,language:"bash"},null,8,["code"]),f(i(Ce),{code:ae.value.videoPython,language:"python"},null,8,["code"])]),_:1})]),_:1},8,["value"])]),_:1},8,["show"])]))}}),Io=Xa(Jr,[["__scopeId","data-v-062c0fc1"]]);export{Io as default};
